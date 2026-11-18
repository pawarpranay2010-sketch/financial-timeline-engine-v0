#!/usr/bin/env python3
"""Platrixa S6 — repeatable local benchmark for the outbound worker.

WHAT IS MEASURED (all on 127.0.0.1, nothing production, no device)
-----------------------------------------------------------------
A real Platrixa API process (uvicorn on loopback) backed by a disposable
embedded PostgreSQL cluster, a real outbound worker using its real HTTP client,
and a SIMULATED llama.cpp-compatible model server on loopback whose latency is
configured. This measures the full protocol path (create → claim → readiness →
model call → fenced commit → result read) without Android hardware or Railway.

Scenarios
  A. model readiness probe latency
  B. one worker, one tenant: per-stage latency, end-to-end, jobs/minute,
     queue wait, p50/p95/p99
  C. empty queue: NO_WORK claim latency
  D. model unavailable: no claim while unready; recovery after the model returns
  E. queue saturation: per-tenant limit rejection with a capacity code
  F. two concurrent workers with distinct owner ids on one tenant
  G. worker restart during processing: expired lease reclaimed, fencing holds
  H. API unreachable (simulated outage): claim fails as a retryable transport error

HONESTY RULES
  * Every number is a LOCAL SIMULATION unless explicitly labeled otherwise.
    The model is a fake server with a configured latency; it is NOT a phone and
    NOT a GGUF model. Token counts are simulated.
  * A single-worker measurement is not evidence of N-developer capacity. The
    capacity section states its assumptions and is a bound, not a promise.
  * No production API, Railway, Termux device, or real credential is used.

Run:
    python scripts/platrixa_worker_benchmark.py [--jobs 20]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import shutil
import socket
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

REPORT_DIR = REPO_ROOT / "reports"

# The 18-field candidate the simulated model "produces". REVIEW_REQUIRED is the
# honest outcome for an input with an unstated payment method.
CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["payment method not stated"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.50",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}
CANDIDATE_JSON = json.dumps(CANDIDATE)
RAW_INPUT = "purchased furniture from raj for rs.25000"


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------


def percentile(sorted_values: list, fraction: float) -> float:
    """Nearest-rank percentile on an already-sorted list."""
    if not sorted_values:
        return 0.0
    index = max(0, min(len(sorted_values) - 1, math.ceil(fraction * len(sorted_values)) - 1))
    return float(sorted_values[index])


def summarize_ms(samples_s: list) -> dict:
    if not samples_s:
        return {"count": 0}
    values = sorted(float(v) * 1000.0 for v in samples_s)
    return {
        "count": len(values),
        "mean_ms": round(statistics.fmean(values), 2),
        "median_ms": round(statistics.median(values), 2),
        "p95_ms": round(percentile(values, 0.95), 2),
        "p99_ms": round(percentile(values, 0.99), 2),
        "min_ms": round(values[0], 2),
        "max_ms": round(values[-1], 2),
    }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# ---------------------------------------------------------------------------
# Simulated model server (loopback only)
# ---------------------------------------------------------------------------


class FakeModel:
    """A loopback llama.cpp-compatible stub with configurable behaviour."""

    def __init__(self, latency_s: float = 0.02, tokens: int = 64) -> None:
        self.state = {"mode": "ok", "latency_s": float(latency_s), "tokens": int(tokens)}
        self.requests = 0
        self.busy_s = 0.0
        self.port = free_port()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _handler(self):
        model = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # noqa: D401 - silence
                return

            def _json(self, status: int, payload: dict) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802 - http.server API
                if self.path == "/v1/models":
                    if model.state["mode"] != "ok":
                        self._json(503, {"error": "model not ready"})
                        return
                    self._json(200, {"object": "list", "data": [{"id": "bench-model"}]})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                if self.path not in ("/v1/chat/completions", "/v1/completions"):
                    self._json(404, {"error": "not found"})
                    return
                model.requests += 1
                if model.state["mode"] != "ok":
                    self._json(503, {"error": "model not ready"})
                    return
                started = time.perf_counter()
                time.sleep(float(model.state["latency_s"]))
                model.busy_s += time.perf_counter() - started
                self._json(
                    200,
                    {
                        "id": "bench-completion",
                        "object": "chat.completion",
                        "model": "bench-model",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": CANDIDATE_JSON},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"completion_tokens": int(model.state["tokens"])},
                    },
                )

        return Handler


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local worker benchmark (S6)")
    parser.add_argument("--jobs", type=int, default=20, help="jobs for the sequential scenario")
    parser.add_argument("--model-latency-ms", type=float, default=20.0)
    args = parser.parse_args(argv)
    jobs_n = max(5, int(args.jobs))

    print("=" * 78)
    print("Platrixa S6 — local worker benchmark (SIMULATED model, real API + PostgreSQL)")
    print("=" * 78)

    import pgserver

    data = Path(f"/tmp/platrixa_bench_pg_{os.getpid()}")
    shutil.rmtree(data, ignore_errors=True)
    data.parent.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(data)
    uri = server.get_uri()
    if "/tmp/" not in uri:
        print(f"refusing non-disposable database: {uri!r}")
        return 1

    env_limits = {
        "PLATRIXA_QUEUE_GLOBAL_LIMIT": "200",
        "PLATRIXA_QUEUE_PER_TENANT_LIMIT": "100",
    }
    os.environ.update(env_limits)
    from backend.auth import gate as metered_gate

    os.environ[metered_gate.METERING_ENV_VAR] = uri
    os.environ["DATABASE_URL"] = uri

    import requests
    import uvicorn
    from sqlalchemy import create_engine, text

    from api.main import create_app
    from backend.auth import api_keys, queue_capacity

    queue_capacity.reset_cache()
    app = create_app()

    api_port = free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning")
    api_server = uvicorn.Server(config)
    api_thread = threading.Thread(target=api_server.run, daemon=True)
    api_thread.start()

    api_base = f"http://127.0.0.1:{api_port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if requests.get(f"{api_base}/v1/health", timeout=1).status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.05)
    else:
        print("API did not become ready")
        return 1

    engine = create_engine(uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True)
    stamp = int(time.time())
    main_key = api_keys.create_api_key(f"bench-main-{stamp}", "bench", "test")["key"]
    sat_key = api_keys.create_api_key(f"bench-sat-{stamp}", "bench-sat", "test")["key"]

    model = FakeModel(latency_s=max(0.001, args.model_latency_ms / 1000.0))
    model.start()

    from platrixa_outbound_inference_worker import (
        MODEL_DIALECT_CHAT,
        OutboundInferenceWorker,
        WorkerConfig,
    )

    def make_worker(api_key: str, worker_id: str, *, api_url: str = api_base, model_url=None):
        cfg = WorkerConfig(
            api_base_url=api_url,
            api_key=api_key,
            worker_id=worker_id,
            model_url=model_url or model.base_url,
            model_id="bench-model",
            model_dialect=MODEL_DIALECT_CHAT,
            model_timeout_s=30.0,
            request_timeout_s=15.0,
            poll_interval_s=0.01,
            max_poll_interval_s=0.05,
            heartbeat_tick_s=0.05,
            shutdown_timeout_s=5.0,
        )
        return OutboundInferenceWorker(cfg, sleep=lambda seconds: time.sleep(min(seconds, 0.05)))

    headers = {"X-Platrixa-API-Key": main_key, "Content-Type": "application/json"}
    sat_headers = {"X-Platrixa-API-Key": sat_key, "Content-Type": "application/json"}
    session = requests.Session()
    results: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "simulated": True,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "database": "embedded PostgreSQL (disposable)",
            "api": f"uvicorn on {api_base}",
            "model": "FakeModel loopback stub (llama.cpp-compatible, SIMULATED)",
            "model_quantization": "n/a (simulated)",
            "model_latency_ms": args.model_latency_ms,
            "worker": "scripts/platrixa_outbound_inference_worker.py (real code path)",
        },
        "scenarios": {},
    }

    def submit(input_text: str, *, hdrs=None) -> dict:
        response = session.post(
            f"{api_base}/v1/inference-jobs",
            headers=hdrs or headers,
            json={"raw_input": input_text},
            timeout=10,
        )
        return response

    def wait_terminal(job_id: str, *, hdrs=None, timeout: float = 60.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            response = session.get(f"{api_base}/v1/jobs/{job_id}", headers=hdrs or headers, timeout=10)
            if response.status_code == 200:
                body = response.json()
                if body.get("status") not in ("PROCESSING", "RETRY_WAIT"):
                    return body
            time.sleep(0.02)
        raise TimeoutError(f"job {job_id} did not finish")

    rss_start = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    cpu_start = time.process_time()

    # ---- A. readiness ------------------------------------------------------
    worker = make_worker(main_key, "bench-worker-a")
    readiness_samples = []
    for _ in range(10):
        started = time.perf_counter()
        ready = worker.model_ready()
        readiness_samples.append(time.perf_counter() - started)
        if not ready.ready:
            raise RuntimeError(f"model unexpectedly unready: {ready.reason}")
    results["scenarios"]["A_readiness"] = {"latency": summarize_ms(readiness_samples)}

    # ---- B. sequential single worker / single tenant -----------------------
    stage = {"submit": [], "claim": [], "model": [], "commit": [], "end_to_end": [], "queue_wait": []}
    outcomes = []
    scenario_started = time.perf_counter()
    for index in range(jobs_n):
        t0 = time.perf_counter()
        response = submit(RAW_INPUT)
        t_submit = time.perf_counter()
        if response.status_code != 202:
            raise RuntimeError(f"submit failed: {response.status_code} {response.text[:200]}")
        job_id = response.json()["job_id"]
        claim_result = worker.claim()
        t_claim = time.perf_counter()
        if claim_result.no_work or claim_result.job is None:
            raise RuntimeError("worker saw NO_WORK during the sequential scenario")
        job = claim_result.job
        body = worker.call_bridge(job.raw_input)
        t_model = time.perf_counter()
        outcome = worker.submit_result(job, body)
        t_commit = time.perf_counter()
        terminal = wait_terminal(job_id)
        t_end = time.perf_counter()

        stage["submit"].append(t_submit - t0)
        stage["claim"].append(t_claim - t_submit)
        stage["model"].append(t_model - t_claim)
        stage["commit"].append(t_commit - t_model)
        stage["end_to_end"].append(t_end - t0)
        stage["queue_wait"].append(t_claim - t_submit)
        outcomes.append({"accepted": outcome.accepted, "status": terminal.get("status")})

    elapsed = time.perf_counter() - scenario_started
    results["scenarios"]["B_single_worker_single_tenant"] = {
        "jobs": jobs_n,
        "elapsed_s": round(elapsed, 3),
        "jobs_per_minute": round(jobs_n / elapsed * 60.0, 2),
        "stage_latency": {name: summarize_ms(values) for name, values in stage.items()},
        "accepted": sum(1 for o in outcomes if o["accepted"]),
        "terminal_statuses": {
            status: sum(1 for o in outcomes if o["status"] == status)
            for status in sorted({o["status"] for o in outcomes})
        },
        "model_server": {
            "requests": model.requests,
            "busy_s": round(model.busy_s, 3),
            "tokens_per_request": model.state["tokens"],
            "simulated_tokens_per_second": round(
                model.state["tokens"] / max(model.state["latency_s"], 1e-6), 1
            ),
        },
    }

    # ---- C. empty queue ----------------------------------------------------
    worker_c = make_worker(main_key, "bench-worker-c")
    empty_samples = []
    for _ in range(5):
        started = time.perf_counter()
        result = worker_c.claim()
        empty_samples.append(time.perf_counter() - started)
        if not result.no_work:
            raise RuntimeError("expected NO_WORK on an empty queue")
    results["scenarios"]["C_empty_queue"] = {"latency": summarize_ms(empty_samples)}

    # ---- D. model unavailable + recovery ----------------------------------
    model.state["mode"] = "down"
    worker_d = make_worker(main_key, "bench-worker-d")
    unready = worker_d.model_ready()
    pending = submit(RAW_INPUT)
    pending_job = pending.json()["job_id"]
    no_claim_report = {"claims": 0, "readiness_waits": 0}
    worker_d.run_forever(max_polls=2)
    no_claim_report = dict(worker_d.last_run_report)
    model.state["mode"] = "ok"
    recovery_started = time.perf_counter()
    worker_d.run_forever(max_polls=3)
    recovered = wait_terminal(pending_job)
    recovery_s = time.perf_counter() - recovery_started
    results["scenarios"]["D_model_unavailable_and_recovery"] = {
        "unready_detected": unready.ready is False,
        "claims_while_unready": no_claim_report["claims"],
        "readiness_waits_while_unready": no_claim_report["readiness_waits"],
        "recovered_status": recovered.get("status"),
        "time_to_complete_after_recovery_s": round(recovery_s, 3),
    }

    # ---- E. queue saturation (separate tenant) ----------------------------
    os.environ["PLATRIXA_QUEUE_PER_TENANT_LIMIT"] = "2"
    os.environ["PLATRIXA_QUEUE_GLOBAL_LIMIT"] = "3"
    queue_capacity.reset_cache()
    sat_responses = [submit(RAW_INPUT, hdrs=sat_headers) for _ in range(3)]
    rejected = sat_responses[-1]
    results["scenarios"]["E_queue_saturation"] = {
        "accepted": sum(1 for r in sat_responses if r.status_code == 202),
        "rejected_status": rejected.status_code,
        "rejected_code": (rejected.json().get("error") or {}).get("code"),
        "retryable": (rejected.json().get("error") or {}).get("retryable"),
    }
    os.environ.update(env_limits)
    queue_capacity.reset_cache()

    # ---- F. two concurrent workers (distinct owner ids, one tenant) --------
    conc_jobs = []
    for _ in range(4):
        response = submit(RAW_INPUT)
        conc_jobs.append(response.json()["job_id"])
    workers = [make_worker(main_key, f"bench-worker-f{i}") for i in (1, 2)]
    thread_results: dict = {}

    def run_worker(name: str, w) -> None:
        thread_results[name] = w.run_forever(max_polls=8)

    started = time.perf_counter()
    threads = [
        threading.Thread(target=run_worker, args=(f"w{i}", w)) for i, w in enumerate(workers, 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    conc_elapsed = time.perf_counter() - started
    terminal = [wait_terminal(job_id) for job_id in conc_jobs]
    check_engine = create_engine(
        uri.replace("postgresql://", "postgresql+psycopg2://", 1), future=True
    )
    with check_engine.connect() as conn:
        # Scoped to THIS scenario's jobs; earlier scenarios also completed jobs.
        completed = conn.execute(
            text(
                "SELECT count(*) FROM platrixa_async_jobs "
                "WHERE status = 'COMPLETED' AND job_id = ANY(:ids)"
            ),
            {"ids": conc_jobs},
        ).scalar_one()
    check_engine.dispose()
    results["scenarios"]["F_two_concurrent_workers"] = {
        "jobs": len(conc_jobs),
        "elapsed_s": round(conc_elapsed, 3),
        "accepted_per_worker": thread_results,
        "terminal_statuses": {
            status: sum(1 for t in terminal if t.get("status") == status)
            for status in sorted({t.get("status") for t in terminal})
        },
        "completed_rows_in_db": int(completed),
    }

    # ---- G. restart during processing -------------------------------------
    restart_submit = submit(RAW_INPUT)
    restart_job = restart_submit.json()["job_id"]
    crashed = make_worker(main_key, "bench-worker-g1")
    claimed = crashed.claim().job
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE platrixa_async_jobs SET lease_expires_at = now() - interval '1 second' "
                "WHERE job_id = :j"
            ),
            {"j": restart_job},
        )
    resumer = make_worker(main_key, "bench-worker-g2")
    recovery_started = time.perf_counter()
    resumed = resumer.claim().job
    outcome = resumer.submit_result(resumed, resumer.call_bridge(resumed.raw_input))
    recovered = wait_terminal(restart_job)
    results["scenarios"]["G_restart_during_processing"] = {
        "first_owner_generation": claimed.lease.generation,
        "second_owner_generation": resumed.lease.generation,
        "generation_advanced": resumed.lease.generation > claimed.lease.generation,
        "commit_accepted": outcome.accepted,
        "final_status": recovered.get("status"),
        "recovery_s": round(time.perf_counter() - recovery_started, 3),
    }

    # ---- H. API unreachable (simulated outage) ----------------------------
    closed_port = free_port()
    offline = make_worker(main_key, "bench-worker-h", api_url=f"http://127.0.0.1:{closed_port}")
    from platrixa_outbound_inference_worker import TransportError

    outage_started = time.perf_counter()
    try:
        offline.claim()
        outage_kind = "unexpected success"
    except TransportError:
        outage_kind = "TransportError"
    results["scenarios"]["H_api_unreachable"] = {
        "failure_kind": outage_kind,
        "latency_s": round(time.perf_counter() - outage_started, 3),
    }

    rss_end = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    cpu_end = time.process_time()
    results["resources"] = {
        "max_rss_kb": int(max(rss_start, rss_end)),
        "cpu_time_s": round(cpu_end - cpu_start, 3),
    }

    # ---- capacity note (explicitly assumption-bound) ----------------------
    e2e = results["scenarios"]["B_single_worker_single_tenant"]["stage_latency"]["end_to_end"]
    measured_mean_s = max(e2e.get("mean_ms", 0) / 1000.0, 1e-6)
    # The only part of end-to-end that is genuinely measured here is API+queue+
    # commit overhead. The model time is CONFIGURED, not measured, so capacity is
    # expressed as a scenario table over plausible DEVICE job durations instead
    # of pretending the simulated latency is the phone's.
    model_latency_s = max(0.001, args.model_latency_ms / 1000.0)
    non_model_overhead_s = max(measured_mean_s - model_latency_s, 0.001)
    scenario_rows = []
    for device_job_s in (5.0, 15.0, 30.0, 45.0, 90.0):
        per_job_s = non_model_overhead_s + device_job_s
        jobs_per_minute = 60.0 / per_job_s
        monthly = jobs_per_minute * 60 * 24 * 30
        # A 100/month developer consumes 1/720 of the month per job; the duty
        # cycle answers "can one worker keep up with 100 developers' VOLUME?"
        duty = (100.0 * 100.0 * per_job_s) / (30 * 24 * 3600)
        scenario_rows.append(
            {
                "assumed_device_job_s": device_job_s,
                "jobs_per_minute": round(jobs_per_minute, 2),
                "jobs_per_month_full_duty": round(monthly, 0),
                "developers_at_100_jobs_per_month": round(monthly / 100.0, 1),
                "duty_cycle_for_100_devs_at_100_jobs": round(duty, 3),
            }
        )
    results["capacity_estimate"] = {
        "basis": (
            "measured local API+queue+commit overhead plus a SCENARIO table over "
            "assumed on-device job durations"
        ),
        "measured_non_model_overhead_ms": round(non_model_overhead_s * 1000.0, 2),
        "measured_simulated_end_to_end_ms": round(measured_mean_s * 1000.0, 2),
        "assumptions": [
            "one worker process running continuously (24/7)",
            "100 admitted jobs per developer per month",
            "512-token generation cap",
            "device job time is NOT measured here; it is the scenario variable",
            "per-tenant pending bound of 10 (default) is not exercised at volume",
        ],
        "scenarios": scenario_rows,
        "warning": (
            "No device inference was measured. The table is arithmetic over assumed "
            "job durations, not evidence of 100-developer support. Burst latency, "
            "the per-tenant queue bound of 10, thermal throttling and model size "
            "are not modeled. Measure on the phone before promising capacity."
        ),
    }
    results["limitations"] = [
        "The model is a loopback stub with configured latency; no GGUF weights were loaded.",
        "Token throughput is simulated (tokens = configured, latency = configured).",
        "API and PostgreSQL are local; Railway network latency and cold starts are not included.",
        "No Android device, Termux, battery, or thermal behaviour was measured.",
        "Scenario D simulates model unavailability only, not a database outage.",
    ]

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"platrixa_worker_benchmark_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    report_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("\nSummary")
    print(f"  readiness mean: {results['scenarios']['A_readiness']['latency']['mean_ms']} ms")
    b = results["scenarios"]["B_single_worker_single_tenant"]
    print(f"  sequential jobs: {b['jobs']} in {b['elapsed_s']}s ({b['jobs_per_minute']}/min)")
    for name, value in b["stage_latency"].items():
        print(f"    {name:12s} mean={value['mean_ms']}ms p95={value['p95_ms']}ms p99={value['p99_ms']}ms")
    print(f"  model-unavailable claims: {results['scenarios']['D_model_unavailable_and_recovery']['claims_while_unready']}")
    print(f"  queue saturation: {results['scenarios']['E_queue_saturation']['rejected_code']}")
    print(f"  concurrent workers completed rows: {results['scenarios']['F_two_concurrent_workers']['completed_rows_in_db']}")
    print(f"  restart recovery: {results['scenarios']['G_restart_during_processing']['recovery_s']}s, final={results['scenarios']['G_restart_during_processing']['final_status']}")
    print("  capacity scenarios (assumed device job time → developers at 100 jobs/month):")
    for row in results["capacity_estimate"]["scenarios"]:
        print(
            f"    {row['assumed_device_job_s']:5.0f}s/job → {row['developers_at_100_jobs_per_month']:8.1f} devs, "
            f"duty={row['duty_cycle_for_100_devs_at_100_jobs']}"
        )
    print(f"\nreport: {report_path}")

    # ---- teardown ---------------------------------------------------------
    api_server.should_exit = True
    api_thread.join(timeout=15)
    model.stop()
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
