#!/usr/bin/env python3
"""
Phase 13 — Hosted developer API: Python client example (HTTP only).

Start the API first (from the repository root):

    uvicorn api.main:app --host 127.0.0.1 --port 8000

Then run this script:

    python3 examples/developer_interface/api_python.py

The client talks to the API over HTTP only — it constructs no internal
backend objects, keeping the transport boundary intact.
"""

from __future__ import annotations

import json
import os

import requests

BASE_URL = os.getenv("PLATRIXA_API_URL", "http://127.0.0.1:8000")
TRANSACTION = "Purchased furniture for cash ₹15,000"


def main() -> None:
    response = requests.post(
        f"{BASE_URL}/v1/process",
        json={"raw_input": TRANSACTION},
        headers={"X-Request-Id": "example-req-1"},
        timeout=120,
    )
    response.raise_for_status()
    result = response.json()

    print(f"api_version : {result['api_version']}")
    print(f"status      : {result['status']}")
    print(f"success     : {result['success']}")
    print(f"request_id  : {result['request_id']}")
    if result.get("accounting"):
        print(f"accounting  : {json.dumps(result['accounting'], default=str)[:200]}")
    if result.get("rule_evidence"):
        print(f"rule_evidence: {len(result['rule_evidence'])} record(s)")


if __name__ == "__main__":
    main()
