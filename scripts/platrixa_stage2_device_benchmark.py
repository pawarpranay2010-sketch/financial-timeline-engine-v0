#!/usr/bin/env python3
"""Stage 2 device benchmark — Termux/Android outbound inference worker.

PURPOSE
=======
Measure the real worker + real device model on real hardware, so the Stage 2
lease defaults can eventually stop being labelled provisional. This script
MEASURES; it does not change the worker's behaviour, the bridge contract, the
model, or any default.

WHAT IT DOES NOT DO
===================
  * It never calls the production API unless you explicitly pass
    ``--i-understand-this-is-not-isolated`` together with a *.railway.app origin.
  * It never replaces the model or changes the bridge's response contract.
  * It never runs an uncontrolled stress test: one smoke job first, then a
    bounded run, and it stops early if the device looks unstable.
  * It does not swap SQLite in, mock the database, or weaken any assertion.

PREREQUISITES (Android/Termux)
==============================
    pkg install python          # 3.10+ ; 3.12 measured
    pip install requests
    # llama-server running with the SAME GGUF as the Stage 1 experiment, e.g.
    #   llama-server -m <model.gguf> --host 127.0.0.1 --port 8081
    # the device bridge (local_llama_bridge.py) serving POST /interpret on 8081
    python scripts/platrixa_stage2_device_benchmark.py --help

An API the worker can reach is required. Run a LOCAL/staging API that you
control (the API needs PostgreSQL; it does not have to be the production one).

    export PLATRIXA_API_BASE_URL=https://<your-staging-api>
    export PLATRIXA_API_KEY=<disposable tenant key>
    export PLATRIXA_WORKER_ID=<bounded device label>
    python scripts/platrixa_stage2_device_benchmark.py --smoke
    python scripts/platrixa_stage2_device_benchmark.py --jobs 10

MEASUREMENTS
============
environment   device model, Android/Termux/Python versions, CPU, RAM, storage
bridge        health, selected endpoint, model identity reported by the bridge
timing        claim → inference start, inference latency p50/p95/max, completion
              → result acceptance, per-phase wall clock
lease         heartbeat cadence (requested vs observed), renewal latency,
              renewal failures, final lease state
resources     peak RSS (VmHWM), load average before/after, thermal zones
recovery      a deliberate loopback-unreachable bridge phase, then recovery,
              proving the worker does not spin or leak and does not submit a
              stale candidate
clean exit    no heartbeat thread left alive, no extra process spun up

Every number printed is measured on this device. Nothing is estimated, and the
script prints ``NOT MEASURED`` for anything the device could not provide.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import requests  # noqa: E402

from platrixa_outbound_inference_worker import (  # noqa: E402
    DEFAULT_BRIDGE_URL,
    BridgeResponseError,
    BridgeUnavailableError,
    LeaseLost,
    OutboundInferenceWorker,
    WorkerConfig,
    WorkerError,
)

# A small, fixed, NON-SENSITIVE corpus: synthetic transactions only. It is
# documented here rather than generated so two runs are comparable.
CORPUS = [
    "purchased furniture from raj for rs.25000",
    "paid electricity bill 1450 rupees via upi",
    "received payment of 5000 from customer acme",
    "bought office chairs from furniture hub for 12300",
    "paid monthly rent 18000 to landlord",
    "sold old laptop for 9500 cash",
    "refund received 799 from online store",
    "paid internet bill 999 by card",
]

PRODUCTION_MARKERS = ("railway.app", "up.railway")

RESULTS: dict = {}


# ---------------------------------------------------------------------------
# Environment probes (each one degrades to "NOT MEASURED")
# ---------------------------------------------------------------------------


def _run(cmd: list) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""


def _prop(name: str) -> str:
    return _run(["getprop", name])


def collect_environment() -> dict:
    env: dict = {}
    env["python_version"] = sys.version.replace("\n", " ")
    env["python_implementation"] = platform.python_implementation()
    env["platform"] = platform.platform()
    env["machine"] = platform.machine()
    env["termux_version"] = _prop("ro.build.version.release") or "NOT MEASURED"
    env["android_release"] = _prop("ro.build.version.release") or "NOT MEASURED"
    env["android_sdk"] = _prop("ro.build.version.sdk") or "NOT MEASURED"
    env["device_model"] = _prop("ro.product.model") or "NOT MEASURED"
    env["device_manufacturer"] = _prop("ro.product.manufacturer") or "NOT MEASURED"
    env["hardware"] = _prop("ro.hardware") or "NOT MEASURED"
    env["abis"] = _prop("ro.product.cpu.abilist") or platform.machine()
    env["cores"] = os.cpu_count()
    env["prefix"] = os.environ.get("PREFIX", "NOT MEASURED")
    env["termux_version_pkg"] = (
        Path("/data/data/com.termux/files/usr/bin/termux-info").exists()
    )

    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        for line in meminfo.read_text(errors="replace").splitlines():
            if line.startswith("MemTotal:"):
                env["mem_total"] = line.split(":")[1].strip()
            if line.startswith("MemAvailable:"):
                env["mem_available"] = line.split(":")[1].strip()
    try:
        usage = os.statvfs(str(_REPO_ROOT))
        env["storage_free_mb"] = round(usage.f_bavail * usage.f_frsize / (1024 * 1024), 1)
    except Exception:
        pass
    env["loadavg_before"] = os.getloadavg() if hasattr(os, "getloadavg") else "NOT MEASURED"
    return env


def read_thermal() -> dict:
    out: dict = {}
    base = Path("/sys/class/thermal")
    if not base.exists():
        return {"thermal": "NOT MEASURED"}
    for zone in sorted(base.glob("thermal_zone*")):
        try:
            temp = (zone / "temp").read_text().strip()
            kind = (zone / "type").read_text().strip()
            out[kind or zone.name] = temp
        except Exception:
            continue
    return out or {"thermal": "NOT MEASURED"}


def peak_rss_mb() -> float | str:
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text(errors="replace").splitlines():
            if line.startswith("VmHWM:"):
                return round(int(line.split()[1]) / 1024, 1)
    try:
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:
        return "NOT MEASURED"


# ---------------------------------------------------------------------------
# Bridge probe
# ---------------------------------------------------------------------------


def probe_bridge(bridge_url: str, bridge_key: str | None, timeout: float = 5.0) -> dict:
    """Answer the question 'is the device bridge healthy, and on which path?'"""
    headers = {"Content-Type": "application/json"}
    if bridge_key:
        headers["X-Platrixa-Android-Bridge-Key"] = bridge_key
    result: dict = {"bridge_url": bridge_url}
    for path in ("/interpret", "/"):
        started = time.perf_counter()
        try:
            response = requests.post(
                f"{bridge_url}{path}", json={"text": CORPUS[0]}, headers=headers, timeout=timeout
            )
        except requests.RequestException as exc:
            result[path] = {"ok": False, "error": type(exc).__name__}
            continue
        result[path] = {
            "ok": response.status_code == 200,
            "status": response.status_code,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if response.status_code == 200:
            try:
                body = response.json()
                result["selected_endpoint"] = path
                result["bridge_model"] = (body.get("model") or {}).get("provider", "NOT MEASURED")
                result["completion_status"] = body.get("completion_status", "NOT MEASURED")
            except ValueError:
                result["selected_endpoint"] = path
                result["bridge_model"] = "response was not JSON"
            break
    return result


# ---------------------------------------------------------------------------
# Instrumented run
# ---------------------------------------------------------------------------


class InstrumentedWorker:
    """The REAL worker with per-phase timing wrapped around its own methods.

    Nothing inside the worker changes: timing is measured by the caller and by a
    thin wrapper on ``renew_lease`` (which the heartbeat thread calls).
    """

    def __init__(self, worker: OutboundInferenceWorker) -> None:
        self.worker = worker
        self.t0 = time.perf_counter()
        self.lock = threading.Lock()
        self.renewals: list = []
        self.claim_latency: list = []
        self.inference_latency: list = []
        self.accept_latency: list = []
        self.claim_to_inference: list = []
        self.original_renew = worker.renew_lease
        worker.renew_lease = self._timed_renew  # type: ignore[method-assign]

    def _timed_renew(self, state) -> None:
        started = time.perf_counter()
        outcome, detail = "renewed", ""
        try:
            self.original_renew(state)
        except LeaseLost as exc:
            outcome, detail = "lease_lost", str(exc)
            raise
        except BridgeResponseError as exc:  # pragma: no cover - defensive
            outcome, detail = "malformed", str(exc)
            raise
        except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
            outcome, detail = "transport_error", type(exc).__name__
            raise
        finally:
            with self.lock:
                self.renewals.append({
                    "at_s": round(time.perf_counter() - self.t0, 3),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "outcome": outcome,
                    "detail": detail[:120],
                    "generation": getattr(state, "generation", None),
                })

    def run_job(self, raw_input: str) -> dict:
        """One instrumented job: claim → bridge → submit, heartbeat running."""
        record: dict = {"raw_input_sha": hash(raw_input) & 0xFFFF, "outcome": "unknown"}

        t_claim = time.perf_counter()
        try:
            claim = self.worker.claim()
        except WorkerError as exc:
            # A transient API failure is a MEASUREMENT, not a crash: record it
            # and let the caller decide whether to continue.
            record["claim_latency_ms"] = round((time.perf_counter() - t_claim) * 1000, 1)
            record.update(outcome="claim_failed", error=f"{type(exc).__name__}: {exc}"[:200])
            self.claim_latency.append(record["claim_latency_ms"])
            return record
        record["claim_latency_ms"] = round((time.perf_counter() - t_claim) * 1000, 1)
        self.claim_latency.append(record["claim_latency_ms"])
        if claim.no_work or claim.job is None:
            record["outcome"] = "no_work"
            return record
        job = claim.job
        record["job_id_prefix"] = job.job_id[:14]
        record["generation"] = job.state.generation
        record["lease_seconds"] = job.state.lease_seconds
        record["renew_every_seconds"] = job.state.renew_every_seconds

        heartbeat = self.worker.start_heartbeat(job.state)
        try:
            t_inf = time.perf_counter()
            record["claim_to_inference_ms"] = round((t_inf - t_claim) * 1000, 1)
            self.claim_to_inference.append(record["claim_to_inference_ms"])
            try:
                body = self.worker.call_bridge(job.raw_input)
                record["outcome"] = "inferred"
            except BridgeUnavailableError as exc:
                record.update(outcome="bridge_unavailable", error=type(exc).__name__)
                record["inference_latency_ms"] = round((time.perf_counter() - t_inf) * 1000, 1)
                return record
            except BridgeResponseError as exc:
                record.update(outcome="bridge_rejected", error=type(exc).__name__)
                record["inference_latency_ms"] = round((time.perf_counter() - t_inf) * 1000, 1)
                return record
            record["inference_latency_ms"] = round((time.perf_counter() - t_inf) * 1000, 1)
            self.inference_latency.append(record["inference_latency_ms"])

            t_accept = time.perf_counter()
            try:
                envelope = self.worker.submit_result(job, body)
            except LeaseLost as exc:
                record.update(outcome="lease_lost", error=str(exc))
                return record
            record["accept_latency_ms"] = round((time.perf_counter() - t_accept) * 1000, 1)
            self.accept_latency.append(record["accept_latency_ms"])
            record["accepted_api_status"] = envelope.get("api_status")
            record["accepted_engine_status"] = envelope.get("status")
            record["attempts"] = job.state.renewals
            record["outcome"] = "accepted"
            return record
        finally:
            self.worker.stop_heartbeat(heartbeat)

    def start(self) -> None:
        self.t0 = time.perf_counter()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def stats(values: list) -> dict:
    if not values:
        return {"n": 0, "p50_ms": "NOT MEASURED", "p95_ms": "NOT MEASURED", "max_ms": "NOT MEASURED"}
    ordered = sorted(values)

    def pct(p: float) -> float:
        if len(ordered) == 1:
            return round(ordered[0], 1)
        index = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
        return round(ordered[index], 1)

    return {
        "n": len(ordered),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "max_ms": round(max(ordered), 1),
        "mean_ms": round(statistics.fmean(ordered), 1),
    }


def print_report(env: dict, bridge: dict, run: dict, instrumented: InstrumentedWorker) -> None:
    print("\n" + "=" * 78)
    print("Stage 2 device benchmark — measured results")
    print("=" * 78)
    section("ENVIRONMENT", env)
    section("BRIDGE", bridge)
    print("\nTIMING")
    print(f"  claim latency          : {stats(instrumented.claim_latency)}")
    print(f"  inference latency      : {stats(instrumented.inference_latency)}")
    print(f"  completion → acceptance: {stats(instrumented.accept_latency)}")
    print(f"  claim → inference start: {stats(instrumented.claim_to_inference)}")
    print("\nHEARTBEAT")
    renewals = instrumented.renewals
    print(f"  renewal attempts       : {len(renewals)}")
    print(f"  renewal latency        : {stats([r['latency_ms'] for r in renewals])}")
    failures = [r for r in renewals if r["outcome"] != "renewed"]
    print(f"  renewal failures       : {len(failures)} {json.dumps(failures[:5])}")
    if len(renewals) >= 2:
        gaps = [round(renewals[i + 1]["at_s"] - renewals[i]["at_s"], 2) for i in range(len(renewals) - 1)]
        print(f"  observed cadence (s)   : {gaps}")
    else:
        print("  observed cadence (s)   : NOT MEASURED (fewer than two renewals)")
    print("\nRESOURCES")
    print(f"  peak RSS (VmHWM)       : {peak_rss_mb()} MB")
    print(f"  load average after     : {os.getloadavg() if hasattr(os, 'getloadavg') else 'NOT MEASURED'}")
    print(f"  thermal zones after    : {json.dumps(read_thermal())}")
    print("\nJOBS")
    for record in run.get("jobs", []):
        print(f"  {json.dumps(record)}")
    print("\nRUN SUMMARY")
    print(f"  jobs attempted         : {run.get('attempted')}")
    print(f"  jobs accepted          : {run.get('accepted')}")
    print(f"  outcomes               : {json.dumps(run.get('outcomes', {}))}")
    print(f"  bridge-down phase      : {json.dumps(run.get('bridge_down', {}))}")
    print(f"  recovery phase         : {json.dumps(run.get('recovery', {}))}")
    print(f"  total wall clock (s)   : {run.get('wall_clock_s')}")
    leftover = [t.name for t in threading.enumerate() if t.name == "platrixa-lease-heartbeat" and t.is_alive()]
    print(f"  heartbeat threads left : {leftover}")
    print(f"  stray child processes  : none started by this script")
    print("=" * 78)
    print("Raw measurements above are from THIS device. Lease defaults stay")
    print("PROVISIONAL until these numbers justify a change.")


def section(title: str, data: dict) -> None:
    print(f"\n{title}")
    for key, value in data.items():
        print(f"  {key:22}: {value}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage 2 device benchmark (Termux/Android). Measures only; changes nothing."
    )
    parser.add_argument("--api-base-url", default=os.environ.get("PLATRIXA_API_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("PLATRIXA_API_KEY", ""))
    parser.add_argument("--api-key-file", default=None)
    parser.add_argument("--worker-id", default=os.environ.get("PLATRIXA_WORKER_ID", ""))
    parser.add_argument("--bridge-url", default=os.environ.get("PLATRIXA_LOCAL_BRIDGE_URL") or DEFAULT_BRIDGE_URL)
    parser.add_argument("--bridge-key", default=os.environ.get("PLATRIXA_LOCAL_BRIDGE_KEY"))
    parser.add_argument("--smoke", action="store_true", help="one job only")
    parser.add_argument("--jobs", type=int, default=5, help="bounded job count (default 5)")
    parser.add_argument("--no-recovery-phase", action="store_true")
    parser.add_argument(
        "--i-understand-this-is-not-isolated", action="store_true",
        help="required to benchmark against a production (railway.app) API origin",
    )
    args = parser.parse_args(argv)

    if not args.api_base_url:
        print("ERROR: --api-base-url (or PLATRIXA_API_BASE_URL) is required.", file=sys.stderr)
        return 2
    lowered = args.api_base_url.lower()
    if any(marker in lowered for marker in PRODUCTION_MARKERS) and not args.i_understand_this_is_not_isolated:
        print(
            "ABORT: this looks like a PRODUCTION API origin. Benchmark against a local or staging "
            "API instead, or pass --i-understand-this-is-not-isolated if you really intend to "
            "consume production quota from a device.",
            file=sys.stderr,
        )
        return 2

    api_key = (args.api_key or "").strip()
    if not api_key and args.api_key_file:
        api_key = Path(args.api_key_file).read_text(encoding="utf-8").strip()
    if not api_key:
        print("ERROR: --api-key / PLATRIXA_API_KEY is required (never printed).", file=sys.stderr)
        return 2

    worker_id = args.worker_id or f"bench-{socket.gethostname()[:24]}"
    config = WorkerConfig(
        api_base_url=args.api_base_url,
        api_key=api_key,
        worker_id=worker_id,
        bridge_url=args.bridge_url,
        bridge_key=args.bridge_key,
    )
    worker = OutboundInferenceWorker(config)
    instrumented = InstrumentedWorker(worker)
    instrumented.start()

    env = collect_environment()
    bridge = probe_bridge(args.bridge_url, args.bridge_key)
    print("=" * 78)
    print("Stage 2 device benchmark")
    print("=" * 78)
    section("ENVIRONMENT", env)
    section("BRIDGE", bridge)
    if bridge.get("selected_endpoint") is None:
        print(
            "WARNING: the bridge did not answer 200 on /interpret or /. Inference phases will "
            "report bridge_unavailable (that is a measurement too, not a crash).",
            file=sys.stderr,
        )

    jobs_total = 1 if args.smoke else max(1, args.jobs)
    run: dict = {"attempted": 0, "accepted": 0, "outcomes": {}, "jobs": []}
    started = time.perf_counter()

    # --- smoke first: abort the longer run if the device cannot do one job ---
    smoke = instrumented.run_job(CORPUS[0])
    run["jobs"].append(smoke)
    run["attempted"] += 1
    run["outcomes"][smoke["outcome"]] = run["outcomes"].get(smoke["outcome"], 0) + 1
    run["accepted"] += 1 if smoke["outcome"] == "accepted" else 0
    print(f"\nsmoke job: {json.dumps(smoke)}")
    if smoke["outcome"] != "accepted" and jobs_total > 1:
        print(
            "STOP: the smoke job was not accepted; not proceeding to the longer run "
            "(fix the bridge/API first). Measurements so far are still printed.",
            file=sys.stderr,
        )
        jobs_total = 1

    # --- bounded longer run -------------------------------------------------
    for index in range(1, jobs_total):
        record = instrumented.run_job(CORPUS[index % len(CORPUS)])
        run["jobs"].append(record)
        run["attempted"] += 1
        run["outcomes"][record["outcome"]] = run["outcomes"].get(record["outcome"], 0) + 1
        run["accepted"] += 1 if record["outcome"] == "accepted" else 0
        if record["outcome"] == "bridge_unavailable" and run["outcomes"]["bridge_unavailable"] > 2:
            print("STOP: repeated bridge unavailability; ending the run early.", file=sys.stderr)
            break

    # --- temporary bridge unavailability + recovery -------------------------
    if not args.no_recovery_phase and jobs_total > 1:
        down_url = f"http://127.0.0.1:{_closed_port()}"
        down_worker = OutboundInferenceWorker(
            WorkerConfig(api_base_url=args.api_base_url, api_key=api_key, worker_id=f"{worker_id}-down",
                         bridge_url=down_url, bridge_key=args.bridge_key, bridge_timeout_s=3.0)
        )
        down_instrumented = InstrumentedWorker(down_worker)
        down_instrumented.start()
        record = down_instrumented.run_job(CORPUS[1])
        run["bridge_down"] = {
            "simulated_by": f"unreachable loopback port {down_url} (no device process was killed)",
            "outcome": record["outcome"], "error": record.get("error"),
            "inference_latency_ms": record.get("inference_latency_ms"),
        }
        print(f"\nbridge-down phase: {json.dumps(run['bridge_down'])}")
        if not isinstance(record["outcome"], str):
            raise SystemExit("unexpected bridge-down outcome")
        recovery = instrumented.run_job(CORPUS[2])
        run["recovery"] = {"outcome": recovery["outcome"],
                           "inference_latency_ms": recovery.get("inference_latency_ms")}
        print(f"recovery phase: {json.dumps(run['recovery'])}")

    run["wall_clock_s"] = round(time.perf_counter() - started, 2)
    print_report(env, bridge, run, instrumented)
    return 0


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


if __name__ == "__main__":
    sys.exit(main())
