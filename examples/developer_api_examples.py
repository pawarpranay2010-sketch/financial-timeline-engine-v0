"""Platrixa developer API examples (Phase 5F).

Eight minimal, reproducible examples of the /v1 developer surface.
Run against a local server:

    python -m uvicorn api.main:app --port 8000
    python examples/developer_api_examples.py --host http://127.0.0.1:8000

These are EXAMPLES/TEST DATA only — no real secrets, no production
statistics. Placeholders: YOUR_API_KEY / YOUR_IDEMPOTENCY_KEY.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import time

import requests

DEFAULT_HOST = "http://127.0.0.1:8000"
# Placeholder credential — replace via env or the --api-key flag.
DEFAULT_KEY = "YOUR_API_KEY"


def _key(args) -> str:
    return args.api_key or os.environ.get("PLATRIXA_API_KEY") or DEFAULT_KEY


def _headers(key: str, idem: str | None = None) -> dict:
    h = {"X-Platrixa-API-Key": key, "Content-Type": "application/json"}
    if idem:
        h["Idempotency-Key"] = idem
    return h


def _show(title: str, r: requests.Response) -> dict:
    print(f"\n== {title} -> HTTP {r.status_code}")
    body = r.json()
    print(json.dumps(body, indent=2, ensure_ascii=False)[:1200])
    return body


# 1. VERIFIED transaction ------------------------------------------------------


def ex_verified(base: str, key: str) -> None:
    r = requests.post(
        f"{base}/v1/process",
        headers=_headers(key),
        json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
        timeout=120,
    )
    body = _show("1. VERIFIED transaction", r)
    if body.get("api_status") == "VERIFIED":
        print("   -> deterministic accounting_result may be posted.")


# 2. REVIEW_REQUIRED transaction ----------------------------------------------


def ex_review_required(base: str, key: str) -> None:
    r = requests.post(
        f"{base}/v1/process",
        headers=_headers(key),
        json={"raw_input": "Paid some money to someone somehow"},
        timeout=120,
    )
    body = _show("2. REVIEW_REQUIRED transaction", r)
    if body.get("api_status") == "REVIEW_REQUIRED":
        print("   -> queue for human review; never auto-post.")


# 3. UNSUPPORTED operation -----------------------------------------------------


def ex_unsupported(base: str, key: str) -> None:
    r = requests.post(
        f"{base}/v1/process",
        headers=_headers(key),
        json={"raw_input": "Blog about blockchain synergies for Q3"},
        timeout=120,
    )
    body = _show("3. UNSUPPORTED operation", r)
    if body.get("api_status") == "UNSUPPORTED":
        print("   -> outside the supported boundary; do not retry.")


# 4. Document submission (async) -----------------------------------------------


def ex_document_submit(base: str, key: str) -> dict | None:
    text = b"INVOICE\nVendor: Sharma Traders\nTotal: Rs. 4,500\nDate: 2026-09-01\n"
    r = requests.post(
        f"{base}/v1/documents",
        headers=_headers(key),
        json={"document_b64": base64.b64encode(text).decode(), "source_name": "invoice.txt"},
        timeout=60,
    )
    body = _show("4. async document submission (202)", r)
    return body if r.status_code == 202 else None


# 5. Polling --------------------------------------------------------------------


def ex_poll(base: str, key: str, job_id: str, *, timeout_s: float = 120) -> dict:
    print(f"\n== 5. polling {job_id}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = requests.get(f"{base}/v1/jobs/{job_id}", headers=_headers(key), timeout=30)
        body = r.json()
        print(f"   status={body.get('status')} retryable={body.get('retryable')}")
        if body.get("status") != "PROCESSING":
            return body
        time.sleep(2)
    raise SystemExit("job did not finish in time")


# 6. Result retrieval ------------------------------------------------------------


def ex_result(base: str, key: str, result_id: str) -> dict | None:
    r = requests.get(f"{base}/v1/results/{result_id}", headers=_headers(key), timeout=60)
    body = _show("6. result retrieval (5D envelope)", r)
    if r.status_code == 200:
        ev = body.get("evidence") or []
        print(f"   -> evidence items: {len(ev)}; lineage: {body.get('lineage')}")
    return body


# 7. Idempotent retry -------------------------------------------------------------


def ex_idempotent_retry(base: str, key: str) -> None:
    idem = "YOUR_IDEMPOTENCY_KEY"
    payload = {"raw_input": "Paid office rent Rs. 25,000 by cheque"}
    r1 = requests.post(f"{base}/v1/process", headers=_headers(key, idem), json=payload, timeout=120)
    r2 = requests.post(f"{base}/v1/process", headers=_headers(key, idem), json=payload, timeout=30)
    _show("7a. idempotent first attempt", r1)
    _show("7b. idempotent replay (same key + same request)", r2)
    print(f"   Idempotent-Replayed: {r1.headers.get('Idempotent-Replayed')} then "
          f"{r2.headers.get('Idempotent-Replayed')} (same request_id: "
          f"{r1.json().get('request_id') == r2.json().get('request_id')})")
    r409 = requests.post(
        f"{base}/v1/process",
        headers=_headers(key, idem),
        json={"raw_input": "A DIFFERENT transaction entirely Rs. 1"},
        timeout=30,
    )
    _show("7c. same key + different request -> 409 conflict", r409)


# 8. Webhook verification (consumer-side) ------------------------------------------


def ex_webhook_verify() -> None:
    secret = "whsec_replace_with_your_registration_secret"
    body = json.dumps(
        {"id": "evt_demo", "type": "document.completed",
         "data": {"job_id": "job_demo", "api_status": "VERIFIED"}},
        sort_keys=True, separators=(",", ":"),
    )
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    print("\n== 8. webhook verification (consumer side)")
    print(f"   header: Platrixa-Signature: {header}")

    def verify(secret: str, header: str, raw: str, tolerance: int = 300) -> bool:
        try:
            parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
            ts = int(parts["t"])
            sig = parts["v1"]
        except Exception:
            return False
        if abs(int(time.time()) - ts) > tolerance:
            return False
        expected = hmac.new(secret.encode(), f"{ts}.{raw}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    print(f"   valid:        {verify(secret, header, body)}")
    print(f"   tampered:     {verify(secret, header, body + ' ')}")
    stale = f"t={ts - 4000},v1={hmac.new(secret.encode(), str(ts - 4000).encode(), hashlib.sha256).hexdigest()}"
    print(f"   stale (replay): {verify(secret, header, body)} with header {stale[:24]}… -> "
          f"{verify(secret, stale, body)}")


def main() -> int:
    p = argparse.ArgumentParser(description="Platrixa developer API examples")
    p.add_argument("--host", default=DEFAULT_HOST)
    p.add_argument("--api-key", default=None, help="or PLATRIXA_API_KEY env; placeholder otherwise")
    args = p.parse_args()

    base = args.host.rstrip("/")
    key = _key(args)
    print("EXAMPLES/TEST DATA ONLY — no real secrets, no production statistics.")
    print(f"host={base}")

    ex_verified(base, key)
    ex_review_required(base, key)
    ex_unsupported(base, key)
    submitted = ex_document_submit(base, key)
    if submitted:
        job = ex_poll(base, key, submitted["job_id"])
        ex_result(base, key, submitted["result_id"])
        print(f"\n   final job status: {job.get('status')}")
    ex_idempotent_retry(base, key)
    ex_webhook_verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
