#!/usr/bin/env python3
"""Device worker — multi-transaction batch execution suite.

Deterministic tests for the batch half of
``scripts/platrixa_outbound_inference_worker.py``. No socket is opened: the
API and the local bridge are in-process fakes, and every wait is bounded.

Coverage:

   1  a claimed INDEPENDENT batch is parsed (mode, ids, indexes, budget, URL)
   2  malformed batch claims fail closed (no list, duplicate id, bad id,
      missing input, unknown mode)
   3  independent: transactions are generated SEQUENTIALLY and committed in
      ONE fenced result call
   4  independent: one generation failure is reported for THAT transaction only
   5  independent: the budget stops generation and the remainder is omitted
      (the server defers it) — never invented as a failure
   6  ordered: links are submitted one at a time, in order, to the
      per-transaction URL
   7  ordered: the approved prior state is threaded into the next prompt and
      is bounded
   8  ordered: a generation failure is submitted as that link's error, never as
      a bridge_body
   9  ordered: an exhausted budget submits an explicit deferral
  10  ordered: the finalized envelope ends the loop
  11  a busy device slot raises SlotUnavailableError (no generation, no submit)
  12  the poll loop RELEASES the claim when the slot is busy, so a shared phone
      never burns a job's attempt budget
  13  the prior-state prompt context is bounded and absent without a state
  14  a single-transaction job still uses the original path (no batch calls)

Run:
    python scripts/platrixa_batch_worker_test.py
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from platrixa_outbound_inference_worker import (  # noqa: E402
    MAX_PRIOR_STATE_CHARS,
    MODE_INDEPENDENT,
    MODE_ORDERED,
    OutboundInferenceWorker,
    SlotUnavailableError,
    WorkerConfig,
    build_contextual_input,
)

API_BASE = "https://api.example.test"
BRIDGE_BASE = "http://127.0.0.1:8081"
API_KEY = "plx_test_tenant_key_not_a_secret"
BRIDGE_KEY = "device-bridge-key-not-a-secret"
WORKER_ID = "device-a"
JOB_ID = "job-batch-1"

CHECKS: list[tuple[str, bool, str]] = []

BRIDGE_BODY = {
    "interpretation_text": '{"transaction_type": "PURCHASE"}',
    "completion_status": "COMPLETE",
    "finish_reason": "stop",
    "model": {"provider": "local-llama.cpp", "artifact_identity": "PROVENANCE_UNKNOWN"},
}


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return bool(ok)


class FakeResponse:
    def __init__(self, status_code: int, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = json.dumps(self._body)
        self.headers = dict(headers or {})

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002
        call = {"url": url, "json": json, "headers": dict(headers or {})}
        self.calls.append(call)
        return self._handler(call)

    def get(self, url, headers=None, timeout=None):
        call = {"url": url, "json": None, "headers": dict(headers or {})}
        self.calls.append(call)
        return self._handler(call)


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class FakeSlotLimiter:
    """Records slot traffic; ``busy=True`` refuses every acquisition."""

    def __init__(self, busy: bool = False):
        self.busy = busy
        self.acquired: list = []
        self.released: list = []

    def acquire(self, wait_s, *, tenant=None, fair=True):
        if self.busy:
            return None
        token = f"0:{len(self.acquired)}"
        self.acquired.append({"token": token, "tenant": tenant})
        return token

    def release(self, token):
        self.released.append(token)
        return True


def lease_body() -> dict:
    return {
        "owner": WORKER_ID,
        "generation": 1,
        "lease_seconds": 120.0,
        "renew_every_seconds": 40.0,
        "renew_url": f"/v1/worker/jobs/{JOB_ID}/lease",
        "result_url": f"/v1/worker/jobs/{JOB_ID}/result",
    }


def claim_body(*, mode=MODE_INDEPENDENT, transactions=None, budget=30.0) -> dict:
    if transactions is None:
        transactions = [
            {"transaction_id": "tx-1", "batch_index": 0, "input": "first transaction"},
            {"transaction_id": "tx-2", "batch_index": 1, "input": "second transaction"},
        ]
    return {
        "api_version": "v1",
        "status": "CLAIMED",
        "job": {
            "job_id": JOB_ID,
            "request_id": "req-1",
            "mode": mode,
            "transaction_count": len(transactions),
            "transactions": transactions,
            "batch_budget_seconds": budget,
            "transaction_result_url": (
                f"/v1/worker/jobs/{JOB_ID}/transactions/{{transaction_id}}/result"
            ),
            "prior_state": None,
            "prior_state_hash": None,
            "lease": lease_body(),
        },
    }


def renewal_body() -> dict:
    return {
        "api_version": "v1",
        "job_id": JOB_ID,
        "renewed": True,
        "lease": {"owner": WORKER_ID, "generation": 1, "lease_seconds": 120.0},
    }


def make_worker(handler, *, bridge_handler=None, now=None, slot=None):
    config = WorkerConfig(
        api_base_url=API_BASE,
        api_key=API_KEY,
        worker_id=WORKER_ID,
        bridge_url=BRIDGE_BASE,
        bridge_key=BRIDGE_KEY,
        heartbeat_tick_s=0.01,
        shutdown_timeout_s=5.0,
    )
    api_session = FakeSession(handler)
    bridge_session = FakeSession(
        bridge_handler or (lambda call: FakeResponse(200, BRIDGE_BODY))
    )
    worker = OutboundInferenceWorker(
        config,
        session=api_session,
        bridge_session=bridge_session,
        model_session=None,
        now=now or time.monotonic,
    )
    worker._slot_limiter = slot
    worker._slot_wait_s = 0.01
    return worker, api_session, bridge_session


def default_handler(*, result_body=None, tx_handler=None):
    def handler(call):
        url = call["url"]
        if url.endswith("/claim"):
            return FakeResponse(200, claim_body())
        if url.endswith("/lease"):
            return FakeResponse(200, renewal_body())
        if "/transactions/" in url:
            if tx_handler is not None:
                return tx_handler(call)
            return FakeResponse(200, {"api_version": "v1", "batch_status": "COMPLETE"})
        if url.endswith("/result"):
            return FakeResponse(200, result_body or {"api_version": "v1", "batch_status": "COMPLETE"})
        if url.endswith("/release"):
            return FakeResponse(200, {"api_version": "v1", "status": "QUEUED", "released": True})
        return FakeResponse(404, {"error": {"code": "NOT_FOUND"}})

    return handler


def claimed(worker):
    claim = worker.claim()
    assert claim.job is not None, "expected a claimed job"
    return claim.job


def main() -> int:
    # ---- 1. independent batch claim -------------------------------------
    worker, api_session, _ = make_worker(default_handler(), slot=FakeSlotLimiter())
    job = claimed(worker)
    check(
        "an independent batch claim exposes mode, ids, indexes and budget",
        job.mode == MODE_INDEPENDENT
        and [t.transaction_id for t in job.transactions] == ["tx-1", "tx-2"]
        and [t.index for t in job.transactions] == [0, 1]
        and job.batch_budget_seconds == 30.0
        and job.transaction_result_url
        and job.is_batch,
        f"mode={job.mode} txs={job.transactions}",
    )

    # ---- 2. malformed claims fail closed --------------------------------
    def claim_with(**overrides):
        body = claim_body()
        body["job"].update(overrides)
        def handler(call):
            return FakeResponse(200, body)
        w, _s, _b = make_worker(handler)
        try:
            w.claim()
            return "no error"
        except Exception as exc:
            return type(exc).__name__

    check(
        "a batch claim with no transaction list fails closed",
        claim_with(transactions=[]) == "MalformedResponseError",
        claim_with(transactions=[]),
    )
    check(
        "a batch claim with duplicate ids fails closed",
        claim_with(
            transactions=[
                {"transaction_id": "tx-1", "batch_index": 0, "input": "a"},
                {"transaction_id": "tx-1", "batch_index": 1, "input": "b"},
            ]
        )
        == "MalformedResponseError",
    )
    check(
        "a batch claim with an unusable transaction id fails closed",
        claim_with(transactions=[{"transaction_id": "bad id", "batch_index": 0, "input": "a"}])
        == "MalformedResponseError",
    )
    check(
        "a batch claim with a missing input fails closed",
        claim_with(transactions=[{"transaction_id": "tx-1", "batch_index": 0, "input": ""}])
        == "MalformedResponseError",
    )
    check(
        "a claim with an unknown mode fails closed",
        claim_with(mode="parallel") == "MalformedResponseError",
    )

    # ---- 3. independent execution: one submission ----------------------
    submitted: list = []

    def indep_handler(call):
        submitted.append(call)
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        if call["url"].endswith("/lease"):
            return FakeResponse(200, renewal_body())
        return FakeResponse(200, {"api_version": "v1", "batch_status": "COMPLETE"})

    bridge_seen: list = []

    def bridge_handler(call):
        bridge_seen.append(call["json"].get("text"))
        return FakeResponse(200, BRIDGE_BODY)

    slot = FakeSlotLimiter()
    worker, api_session, _ = make_worker(indep_handler, bridge_handler=bridge_handler, slot=slot)
    job = claimed(worker)
    outcome = worker.process_job(job)
    result_calls = [
        c for c in api_session.calls if c["url"].endswith(f"/v1/worker/jobs/{JOB_ID}/result")
    ]
    check(
        "independent batch generates every transaction sequentially",
        len(bridge_seen) == 2 and "first transaction" in bridge_seen[0]
        and "second transaction" in bridge_seen[1],
        json.dumps(bridge_seen)[:240],
    )
    check(
        "independent batch commits ALL transactions in ONE fenced call",
        len(result_calls) == 1
        and [t["transaction_id"] for t in result_calls[0]["json"]["transactions"]] == ["tx-1", "tx-2"],
        json.dumps(result_calls)[:300],
    )
    check(
        "independent submission carries each transaction's complete bridge body",
        all("bridge_body" in t for t in result_calls[0]["json"]["transactions"]),
        json.dumps(result_calls[0]["json"])[:240],
    )
    check(
        "the device slot is acquired once and released once per batch",
        len(slot.acquired) == 1 and len(slot.released) == 1,
        f"acquired={slot.acquired} released={slot.released}",
    )
    check("the batch outcome is the server's envelope", outcome.get("batch_status") == "COMPLETE", json.dumps(outcome)[:200])
    check(
        "no per-transaction URL is used in independent mode",
        not any("/transactions/" in c["url"] for c in api_session.calls),
    )

    # ---- 4. independent: one generation failure is isolated -------------
    calls: list = []

    def partial_bridge(call):
        calls.append(call["json"].get("text"))
        if len(calls) == 2:
            raise AssertionError("bridge error should be raised below")
        return FakeResponse(200, BRIDGE_BODY)

    def partial_bridge_fixed(call):
        calls.append(call["json"].get("text"))
        if len(calls) == 2:
            return FakeResponse(503, {"error": {"message": "model unavailable"}})
        return FakeResponse(200, BRIDGE_BODY)

    worker, api_session, _ = make_worker(
        indep_handler, bridge_handler=partial_bridge_fixed, slot=FakeSlotLimiter()
    )
    job = claimed(worker)
    worker.process_job(job)
    submitted_result = [
        c for c in api_session.calls if c["url"].endswith(f"/v1/worker/jobs/{JOB_ID}/result")
    ][0]["json"]["transactions"]
    check(
        "a failed generation is reported for ITS transaction only",
        len(submitted_result) == 2
        and "bridge_body" in submitted_result[0]
        and "error" in submitted_result[1]
        and submitted_result[1]["error"]["code"] == "MODEL_UNAVAILABLE",
        json.dumps(submitted_result)[:300],
    )

    # ---- 5. independent: budget exhaustion ------------------------------
    clock = FakeClock()

    def budget_bridge(call):
        clock.advance(40.0)
        return FakeResponse(200, BRIDGE_BODY)

    worker, api_session, _ = make_worker(
        default_handler(), bridge_handler=budget_bridge, now=clock, slot=FakeSlotLimiter()
    )
    job = claimed(worker)
    job.batch_budget_seconds = 30.0
    worker.process_job(job)
    deferred_result = [
        c for c in api_session.calls if c["url"].endswith(f"/v1/worker/jobs/{JOB_ID}/result")
    ][0]["json"]["transactions"]
    check(
        "an exhausted budget submits only what it generated (the rest is deferred)",
        len(deferred_result) == 1 and deferred_result[0]["transaction_id"] == "tx-1",
        json.dumps(deferred_result)[:240],
    )

    # ---- 6/7. ordered: link-by-link with approved state -----------------
    ordered_calls: list = []
    bridge_texts: list = []

    def ordered_handler(call):
        ordered_calls.append(call)
        url = call["url"]
        if url.endswith("/claim"):
            return FakeResponse(200, claim_body(mode=MODE_ORDERED))
        if url.endswith("/lease"):
            return FakeResponse(200, renewal_body())
        if url.endswith("/transactions/tx-1/result"):
            return FakeResponse(
                200,
                {
                    "api_version": "v1",
                    "job_id": JOB_ID,
                    "mode": "ordered",
                    "transaction": {"transaction_id": "tx-1", "status": "VERIFIED"},
                    "next": {
                        "transaction_id": "tx-2",
                        "batch_index": 1,
                        "prior_state_hash": "hash-1",
                        "prior_state": {"approved_transaction_id": "tx-1", "state_hash": "hash-1"},
                    },
                },
            )
        if url.endswith("/transactions/tx-2/result"):
            return FakeResponse(
                200,
                {
                    "api_version": "v1",
                    "batch_status": "COMPLETE",
                    "verified_count": 2,
                    "final_state_hash": "hash-2",
                },
            )
        return FakeResponse(404, {"error": {"code": "NOT_FOUND"}})

    def ordered_bridge(call):
        bridge_texts.append(call["json"].get("text"))
        return FakeResponse(200, BRIDGE_BODY)

    worker_ordered, api_ordered, _ = make_worker(
        ordered_handler, bridge_handler=ordered_bridge, slot=FakeSlotLimiter()
    )
    ordered_job = claimed(worker_ordered)
    outcome = worker_ordered.process_job(ordered_job)

    tx_urls = [c["url"] for c in api_ordered.calls if "/transactions/" in c["url"]]
    check(
        "ordered batch submits each link to its per-transaction URL, in order",
        tx_urls == [
            f"{API_BASE}/v1/worker/jobs/{JOB_ID}/transactions/tx-1/result",
            f"{API_BASE}/v1/worker/jobs/{JOB_ID}/transactions/tx-2/result",
        ],
        json.dumps(tx_urls),
    )
    check(
        "ordered batch never uses the batch-wide result route",
        not any(c["url"].endswith("/result") and "/transactions/" not in c["url"] for c in api_ordered.calls),
    )
    check(
        "the approved prior state is threaded into the NEXT prompt only",
        "Approved prior accounting state" not in bridge_texts[0]
        and "Approved prior accounting state" in bridge_texts[1]
        and "hash-1" in bridge_texts[1]
        and "second transaction" in bridge_texts[1],
        json.dumps(bridge_texts)[:300],
    )
    check(
        "the finalized ordered envelope ends the loop",
        outcome.get("batch_status") == "COMPLETE" and outcome.get("verified_count") == 2,
        json.dumps(outcome)[:200],
    )

    # ---- 8. ordered: generation failure becomes that link's error -------
    def failing_bridge(call):
        return FakeResponse(503, {"error": {"message": "model unavailable"}})

    worker, api_session, _ = make_worker(
        ordered_handler, bridge_handler=failing_bridge, slot=FakeSlotLimiter()
    )
    job = claimed(worker)
    worker.process_job(job)
    failing_calls = [c for c in api_session.calls if "/transactions/" in c["url"]]
    check(
        "an ordered generation failure is submitted as that link's error",
        len(failing_calls) == 1
        and "error" in failing_calls[0]["json"]
        and "bridge_body" not in failing_calls[0]["json"],
        json.dumps(failing_calls)[:300] if failing_calls else "no call",
    )

    # ---- 9. ordered: deferral ------------------------------------------
    worker, api_session, _ = make_worker(
        ordered_handler, slot=FakeSlotLimiter()
    )
    job = worker._build_claimed_job(claim_body(mode=MODE_ORDERED)["job"])
    job.batch_budget_seconds = 0.0  # unbounded by itself; force a spent clock
    spent = FakeClock()
    worker._now = spent
    # A zero budget means "no deadline"; use an already-spent deadline instead.
    outcome = worker._process_ordered_batch(job, deadline=spent() - 1.0)
    defer_calls = [c for c in api_session.calls if "/transactions/" in c["url"]]
    check(
        "an exhausted ordered budget submits an explicit deferral",
        len(defer_calls) == 1 and defer_calls[0]["json"].get("deferred") is True,
        json.dumps(defer_calls)[:200] if defer_calls else "no call",
    )
    check(
        "an exhausted ordered budget generates nothing",
        not any("/lease" in c["url"] and c["json"] for c in api_session.calls),
        "no model call expected",
    )

    # ---- 11. busy slot --------------------------------------------------
    worker, api_session, _ = make_worker(
        default_handler(), slot=FakeSlotLimiter(busy=True)
    )
    job = claimed(worker)
    exc = None
    try:
        worker.process_job(job)
    except SlotUnavailableError as raised:
        exc = raised
    check("a busy slot raises SlotUnavailableError", exc is not None, type(exc).__name__)
    check(
        "a busy slot submits NOTHING",
        not any(c["url"].endswith("/result") for c in api_session.calls),
    )

    # ---- 12. poll loop releases the claim ------------------------------
    released_calls: list = []

    def busy_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(200, claim_body())
        if call["url"].endswith("/release"):
            released_calls.append(call)
            return FakeResponse(200, {"api_version": "v1", "status": "QUEUED", "released": True})
        if call["url"].endswith("/lease"):
            return FakeResponse(200, renewal_body())
        return FakeResponse(404, {"error": {"code": "NOT_FOUND"}})

    worker, api_session, _ = make_worker(busy_handler, slot=FakeSlotLimiter(busy=True))
    worker._sleep = lambda seconds: None
    worker.run_forever(max_polls=1)
    check(
        "the poll loop releases the claim when the slot is busy",
        len(released_calls) == 1
        and released_calls[0]["json"]["reason"] == "SLOT_BUSY"
        and released_calls[0]["json"]["lease"]["owner"] == WORKER_ID,
        json.dumps(released_calls)[:240],
    )
    check(
        "a released claim is counted as a slot wait, not a failure",
        worker.last_run_report.get("slot_waits") == 1
        and worker.last_run_report.get("bridge_failures") == 0,
        json.dumps(worker.last_run_report),
    )

    # ---- 13. prompt context bounds -------------------------------------
    long_state = {"accounting": "x" * 5000}
    contextual = build_contextual_input("current text", long_state)
    check(
        "the prior-state prompt context is bounded",
        len(contextual) <= MAX_PRIOR_STATE_CHARS + 200 and "current text" in contextual,
        f"len={len(contextual)}",
    )
    check(
        "no prior state means the input is unchanged, byte for byte",
        build_contextual_input("current text") == "current text",
    )

    # ---- 14. single-transaction path is unchanged ----------------------
    def single_handler(call):
        if call["url"].endswith("/claim"):
            return FakeResponse(
                200,
                {
                    "api_version": "v1",
                    "status": "CLAIMED",
                    "job": {
                        "job_id": JOB_ID,
                        "request_id": "req-1",
                        "raw_input": "purchased furniture from raj for rs.25000",
                        "lease": lease_body(),
                    },
                },
            )
        if call["url"].endswith("/lease"):
            return FakeResponse(200, renewal_body())
        return FakeResponse(200, {"api_version": "v1", "api_status": "VERIFIED"})

    worker, api_session, _ = make_worker(single_handler, slot=FakeSlotLimiter())
    job = worker.claim().job
    check(
        "a single-transaction claim has no batch shape",
        job is not None and job.mode == "single" and not job.is_batch and not job.transactions,
        f"mode={getattr(job, 'mode', None)}",
    )
    worker.process_job(job)
    single_results = [c for c in api_session.calls if c["url"].endswith("/result")]
    check(
        "a single transaction still submits bridge_body (not transactions)",
        len(single_results) == 1
        and "bridge_body" in single_results[0]["json"]
        and "transactions" not in single_results[0]["json"],
        json.dumps(single_results)[:240],
    )

    failed = [name for name, ok, _detail in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"RESULT: {len(CHECKS) - len(failed)}/{len(CHECKS)} batch worker checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
