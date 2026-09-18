#!/usr/bin/env python3
"""Standalone external Platrixa API client — the developer integration proof.

Uses ONLY the Python standard library (urllib): no Platrixa imports, no
model access, no SDK. This is exactly what an external customer writes:

    External application
        → HTTPS endpoint
        → X-Platrixa-API-Key header
        → JSON request/response

Usage:
    export PLATRIXA_API_KEY=plx_live_...        # your key (never printed)
    python3 external_client.py \
        --base https://financial-timeline-engine-v0-production.up.railway.app \
        --text "Paid 12,500 to Raj for office furniture by cheque."

The key is read from the PLATRIXA_API_KEY environment variable (or
--key-file). It is sent only in the request header and is never printed,
logged, or written by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://financial-timeline-engine-v0-production.up.railway.app"


def main() -> int:
    parser = argparse.ArgumentParser(description="Minimal external Platrixa API client")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API base URL")
    parser.add_argument("--text", required=True, help="raw financial input text")
    parser.add_argument(
        "--key-file",
        default=None,
        help="file containing the API key (alternative to PLATRIXA_API_KEY env var)",
    )
    args = parser.parse_args()

    api_key = (os.environ.get("PLATRIXA_API_KEY", "") or "").strip()
    if not api_key and args.key_file:
        with open(args.key_file, "r", encoding="utf-8") as fh:
            api_key = fh.read().strip()
    if not api_key:
        print(
            "ERROR: no API key. Set PLATRIXA_API_KEY or pass --key-file.",
            file=sys.stderr,
        )
        return 2

    url = args.base.rstrip("/") + "/v1/process"
    body = json.dumps({"raw_input": args.text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Platrixa-API-Key": api_key,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            status = resp.status
            payload = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        payload = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        print(f"NETWORK ERROR: {exc.reason}", file=sys.stderr)
        return 1

    print(f"HTTP {status}")
    try:
        print(json.dumps(json.loads(payload), indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
