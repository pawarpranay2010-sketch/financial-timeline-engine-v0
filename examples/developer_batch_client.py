#!/usr/bin/env python3
"""Platrixa Developer API — minimal batch client (public contract only).

What it demonstrates, end to end, against the REAL Developer API:

  1. submit a bounded batch (independent or ordered);
  2. poll the job until it is terminal, honouring the server's pacing;
  3. print every transaction's own outcome (never invented);
  4. separate REVIEW_REQUIRED (and other non-verified) results from verified ones;
  5. handle failures, quota errors and retries with idempotency;
  6. show job/transaction usage from the server's own counters.

It uses no private endpoint. Configure it with environment variables only:

    export PLATRIXA_BASE_URL="https://<beta-host>"
    export PLATRIXA_API_KEY="plx_test_REPLACE_WITH_YOUR_KEY"

    python3 examples/developer_batch_client.py
    python3 examples/developer_batch_client.py --mode ordered

Exit codes: 0 the batch completed (whatever the individual outcomes),
1 the job failed / could not be submitted, 2 configuration or usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

TERMINAL_STATUSES = {"COMPLETED", "FAILED"}


def _request(
    base: str,
    path: str,
    key: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    idempotency_key: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict, dict]:
    """One HTTP call returning ``(status, json_body, headers)``.

    Deliberately stdlib-only: the example must not depend on a client library
    that could drift from the documented contract.
    """
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"{base}{path}", data=data, method=method)
    request.add_header("X-Platrixa-API-Key", key)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if idempotency_key:
        request.add_header("Idempotency-Key", idempotency_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            headers = {k.lower(): v for k, v in response.headers.items()}
            return response.status, json.loads(raw or b"{}"), headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw or b"{}")
        except Exception:
            payload = {}
        return exc.code, payload, {k.lower(): v for k, v in (exc.headers or {}).items()}


def submit_batch(base: str, key: str, mode: str, idempotency_key: str) -> dict:
    """Submit the batch, retrying only what is safe to retry.

    Same idempotency key on every attempt: a timeout does not create a second
    batch, and a replay returns the original job reference.
    """
    transactions = [
        {"transaction_id": "tx-001", "input": "Started business with cash Rs. 10000"},
        {"transaction_id": "tx-002", "input": "Purchased goods for cash Rs. 2000"},
        {"transaction_id": "tx-003", "input": "Sold goods for cash Rs. 3000"},
    ]
    last_error = "submission did not succeed"
    for attempt in range(1, 5):
        status, body, headers = _request(
            base,
            "/v1/inference-jobs",
            key,
            method="POST",
            body={"mode": mode, "transactions": transactions},
            idempotency_key=idempotency_key,
        )
        if status == 202:
            if headers.get("idempotent-replayed") == "true":
                print("  (replay: this idempotency key already created this job)")
            return body
        code = (body.get("error") or {}).get("code")
        details = (body.get("error") or {}).get("details")

        if status == 409 and code == "IDEMPOTENCY_REQUEST_IN_PROGRESS":
            time.sleep(2)  # the first attempt is still being admitted
            continue
        if status == 409:
            raise SystemExit(f"idempotency conflict: {body}")
        if status == 429:
            wait = float(headers.get("retry-after") or 2 ** attempt)
            print(f"  throttled ({code}); waiting {wait:.0f}s")
            time.sleep(min(wait, 30))
            last_error = code or "throttled"
            continue
        if status == 503:
            time.sleep(min(2 ** attempt, 20))
            last_error = code or "unavailable"
            continue
        if status == 400:
            raise SystemExit(
                f"batch rejected: {code} {body.get('error', {}).get('message', '')}"
                + (f" details={details}" if details else "")
            )
        raise SystemExit(f"unexpected submission response: {status} {body}")
    raise SystemExit(f"could not submit the batch: {last_error}")


def poll_job(base: str, key: str, job_id: str, *, timeout_s: float = 600.0) -> dict:
    """Poll until terminal, with bounded backoff and the server's pacing hint."""
    deadline = time.monotonic() + timeout_s
    delay = 2.0
    while time.monotonic() < deadline:
        status, body, _headers = _request(base, f"/v1/jobs/{job_id}", key)
        if status != 200:
            raise SystemExit(f"job status unavailable: {status} {body}")
        state = body.get("status")
        batch = body.get("batch") or {}
        counts = batch.get("counts") or {}
        if counts:
            print(
                f"  [{state}] processed={sum(counts.values()) - counts.get('pending', 0)}"
                f"/{batch.get('total_transactions')} "
                f"verified={counts.get('verified', 0)} "
                f"review={counts.get('review_required', 0)} "
                f"failed={counts.get('failed', 0)} "
                f"blocked={counts.get('blocked', 0)} "
                f"deferred={counts.get('deferred', 0)}"
            )
        else:
            print(f"  [{state}]")
        if state in TERMINAL_STATUSES:
            return body
        if state == "RETRY_WAIT":
            # A retryable infrastructure failure is already scheduled server-side.
            pass
        time.sleep(delay)
        delay = min(delay * 1.5, 10.0)
    raise SystemExit("timed out waiting for the job; keep polling /v1/jobs/{job_id}")


def report_results(base: str, key: str, result_id: str) -> int:
    """Fetch the batch envelope and every per-transaction result."""
    status, envelope, _headers = _request(base, f"/v1/results/{result_id}", key)
    if status != 200:
        raise SystemExit(f"result unavailable: {status} {envelope}")

    print("\nBatch outcome")
    print(f"  batch_status : {envelope.get('batch_status')}")
    print(f"  api_status   : {envelope.get('api_status')}")
    print(f"  counts       : {json.dumps(envelope.get('counts'))}")
    print(f"  quota        : {json.dumps(envelope.get('quota'))}")
    if envelope.get("final_state_hash"):
        print(f"  state chain  : {len(envelope.get('state_chain') or [])} link(s), "
              f"final={envelope['final_state_hash'][:16]}…")

    verified, review, other = [], [], []
    for summary in envelope.get("transactions") or []:
        tx_id = summary.get("transaction_id")
        state = summary.get("status")
        line = f"  {tx_id:>8}  {state:<16} {summary.get('error_code') or ''}"
        print(line.rstrip())

        result_url = summary.get("result_url")
        if not result_url:
            other.append((tx_id, state, summary.get("error_code")))
            continue
        tx_status, tx_envelope, _h = _request(base, result_url, key)
        if tx_status != 200:
            other.append((tx_id, state, (tx_envelope.get("error") or {}).get("code")))
            continue
        if state == "VERIFIED":
            verified.append((tx_id, tx_envelope))
        elif state == "REVIEW_REQUIRED":
            review.append((tx_id, tx_envelope))
        else:
            other.append((tx_id, state, summary.get("error_code")))

    print("\nWhat to do with these results")
    for tx_id, tx in verified:
        accounting = tx.get("accounting") or {}
        print(f"  {tx_id}: VERIFIED — post it. accounting keys: {sorted(accounting)[:6]}")
    for tx_id, tx in review:
        print(f"  {tx_id}: REVIEW_REQUIRED — send to a human; issues: "
              f"{', '.join((tx.get('issues') or [])[:3]) or 'none listed'}")
    for tx_id, state, code in other:
        print(f"  {tx_id}: {state}{f' ({code})' if code else ''} — do not post")
    return 0 if envelope.get("batch_status") in ("COMPLETE", "PARTIAL", "HALTED") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa batch client example")
    parser.add_argument("--mode", choices=("independent", "ordered"), default="independent")
    parser.add_argument(
        "--idempotency-key",
        default=f"example-batch-{int(time.time())}",
        help="Re-use the same value when retrying this exact batch",
    )
    args = parser.parse_args(argv)

    base = (os.environ.get("PLATRIXA_BASE_URL") or "").rstrip("/")
    key = os.environ.get("PLATRIXA_API_KEY") or ""
    if not base or not key:
        print(
            "Set PLATRIXA_BASE_URL and PLATRIXA_API_KEY (see docs/DEVELOPER_QUICKSTART.md).",
            file=sys.stderr,
        )
        return 2

    print(f"Submitting a {args.mode} batch to {base}")
    accepted = submit_batch(base, key, args.mode, args.idempotency_key)
    job_id = accepted["job_id"]
    result_id = accepted["result_id"]
    print(f"  job_id={job_id} transactions={accepted.get('transaction_count')} "
          f"mode={accepted.get('mode')}")

    print("\nPolling")
    job = poll_job(base, key, job_id)
    if job.get("status") == "FAILED":
        print(f"  job failed: {job.get('reason_codes')}")
        return 1

    return report_results(base, key, result_id)


if __name__ == "__main__":
    sys.exit(main())
