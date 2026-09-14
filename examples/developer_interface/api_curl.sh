#!/usr/bin/env bash
# Phase 13 — Hosted developer API: curl client example.
#
# Start the API first (from the repository root):
#   uvicorn api.main:app --host 127.0.0.1 --port 8000
#
# Then run this script:
#   sh examples/developer_interface/api_curl.sh

set -eu

BASE_URL="${PLATRIXA_API_URL:-http://127.0.0.1:8000}"

curl -s -X POST "${BASE_URL}/v1/process" \
  -H "Content-Type: application/json" \
  -H "X-Request-Id: example-req-1" \
  -d '{"raw_input": "Purchased furniture for cash ₹15,000"}'

echo
