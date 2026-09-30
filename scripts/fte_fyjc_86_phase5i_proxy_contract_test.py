#!/usr/bin/env python3
"""
Phase 5I — Proxy header contract — evidence suite (fte_fyjc_86).

Drives the REAL production proxy modules with Node (no reimplemented
forwarding logic):
  * frontend/functions/v1/[[path]].js  (Cloudflare Pages /v1)
  * frontend/functions/api/[[path]].js (Cloudflare Pages /api)
  * frontend/web/lib/platrixa-proxy.ts (Next dev/preview /v1 + /api)

Anti-vacuous proofs (audit C2):
  P1 a tenant request forwards the caller's X-Platrixa-API-Key UNCHANGED
  P2 a management request forwards X-Platrixa-Management-Token
  P3 idempotency-key and x-request-id survive
  P4 ABSENT headers stay absent (no fabricated identity ever injected)
  P5 the operator PLATRIXA_API_KEY is a FALLBACK ONLY — never a
     substitute for a caller-supplied tenant key
  P6 the management token is never replaced by anything
  P7 response contract headers (Idempotent-Replayed, Retry-After,
     X-Platrixa-Error) pass back through the Next proxy
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


PROBE_JS = r"""
import { onRequest as v1onRequest } from "../frontend/functions/v1/[[path]].js";
import { onRequest as apionRequest } from "../frontend/functions/api/[[path]].js";

const out = { captures: [] };
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {
  out.captures.push({
    fn: init.__fn,
    url: String(url),
    headers: Object.fromEntries(new Headers(init.headers).entries()),
  });
  return new Response(JSON.stringify({ ok: true, replayed: "true" }), {
    status: 200,
    headers: {
      "content-type": "application/json",
      "idempotent-replayed": "true",
      "retry-after": "2",
      "x-platrixa-error": "QUOTA_EXHAUSTED",
    },
  });
};

const TENANT = "plx_test_tenant0123456789";
const MGMT = "mgmt_token_abcdef1234567890";
const IDEN = "idem-key-0123456789abcdef";

async function pagesProbe(fn, path, headers, backendEnv) {
  const req = new Request("https://platrixa.pages.dev" + path, {
    method: "POST",
    headers,
    body: JSON.stringify({ raw_input: "x" }),
  });
  const handler = fn === "v1" ? v1onRequest : apionRequest;
  await handler({ request: req, env: backendEnv });
}

// P1/P3: tenant request with all four identity headers.
await pagesProbe("v1", "/v1/process", {
  "content-type": "application/json",
  "x-platrixa-api-key": TENANT,
  "x-platrixa-management-token": MGMT,
  "idempotency-key": IDEN,
  "x-request-id": "corr-123",
}, { API_BACKEND_URL: "https://api.example.com" });

// P4: tenant request with NO identity headers at all.
await pagesProbe("v1", "/v1/process", { "content-type": "application/json" },
  { API_BACKEND_URL: "https://api.example.com" });

// P6 probe on the /api function.
await pagesProbe("api", "/api/v1/kernel/process", {
  "content-type": "application/json",
  "x-platrixa-api-key": TENANT,
}, { API_BACKEND_URL: "https://api.example.com" });

// ---- Next dev/preview proxy ----
const { forwardToPlatrixa, proxyResponse } = await import(
  "../frontend/web/lib/platrixa-proxy.ts"
);

async function nextProbe(headers, env, path) {
  const req = new Request("https://frontend.example.com" + path, {
    method: "POST",
    headers,
    body: JSON.stringify({ raw_input: "x" }),
  });
  req.nextUrl = new URL("https://frontend.example.com" + path);
  const outcome = await forwardToPlatrixa(req, [], "");
  const resp = proxyResponse(outcome);
  return {
    captured: out.captures[out.captures.length - 1],
    responseHeaders: Object.fromEntries(resp.headers.entries()),
  };
}

process.env.PLATRIXA_API_BASE_URL = "https://api.example.com";

// P5a: caller-supplied tenant key + operator key configured → tenant key WINS.
process.env.PLATRIXA_API_KEY = "plx_dev_operator_shared_key";
await nextProbe({
  "content-type": "application/json",
  "x-platrixa-api-key": TENANT,
  "x-platrixa-management-token": MGMT,
  "idempotency-key": IDEN,
  "x-request-id": "corr-123",
}, {}, "/v1/process");

// P5b: NO caller key + operator key configured → fallback applies (documented).
await nextProbe({ "content-type": "application/json" }, {}, "/v1/process");

// P5c: NO caller key + NO operator key → nothing is fabricated.
process.env.PLATRIXA_API_KEY = "";
await nextProbe({ "content-type": "application/json" }, {}, "/v1/process");

// P7: management-plane call; response contract headers pass back.
const mgmtResult = await nextProbe({
  "content-type": "application/json",
  "x-platrixa-management-token": MGMT,
}, {}, "/v1/developer/api-keys");

console.log("PROBE_JSON_BEGIN");
console.log(JSON.stringify({
  captures: out.captures,
  nextResponses: { mgmt: mgmtResult.responseHeaders },
}));
console.log("PROBE_JSON_END");
globalThis.fetch = realFetch;
"""


def run_probe() -> dict:
    with tempfile.TemporaryDirectory() as td:
        # Probe must live INSIDE the repo so its relative imports of the
        # real proxy modules resolve; it is a throwaway file, removed on
        # context exit.
        probe_path = ROOT / "scripts" / "tmp_5i_probe.mjs"
        probe_path.write_text(PROBE_JS, encoding="utf-8")
        try:
            proc = subprocess.run(
                ["node", str(probe_path)],
                cwd=str(ROOT / "scripts"),
                capture_output=True,
                text=True,
                timeout=120,
            )
        finally:
            probe_path.unlink(missing_ok=True)
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            raise SystemExit("probe failed")
        text = proc.stdout
        begin = text.index("PROBE_JSON_BEGIN") + len("PROBE_JSON_BEGIN")
        end = text.index("PROBE_JSON_END")
        import json

        return json.loads(text[begin:end].strip())


def main() -> int:
    print("Phase 5I — proxy header contract (real proxy modules, real forwarding path)")
    data = run_probe()
    caps = data["captures"]
    pages_v1_tenant = caps[0]
    pages_v1_anon = caps[1]
    pages_api = caps[2]
    next_tenant = caps[3]
    next_fallback = caps[4]
    next_none = caps[5]
    next_mgmt = caps[6]

    TENANT = "plx_test_tenant0123456789"
    MGMT = "mgmt_token_abcdef1234567890"
    IDEN = "idem-key-0123456789abcdef"

    print("\nP — Cloudflare Pages /v1 function")
    h = pages_v1_tenant["headers"]
    check("P1 tenant API key forwarded UNCHANGED", h.get("x-platrixa-api-key") == TENANT,
          str(h.get("x-platrixa-api-key")))
    check("P2 management token forwarded UNCHANGED", h.get("x-platrixa-management-token") == MGMT,
          str(h.get("x-platrixa-management-token")))
    check("P3 idempotency-key + x-request-id forwarded",
          h.get("idempotency-key") == IDEN and h.get("x-request-id") == "corr-123",
          f"{h.get('idempotency-key')}/{h.get('x-request-id')}")
    check("P4 no headers in → no fabricated identity out",
          "x-platrixa-api-key" not in pages_v1_anon["headers"]
          and "x-platrixa-management-token" not in pages_v1_anon["headers"]
          and "idempotency-key" not in pages_v1_anon["headers"],
          str(pages_v1_anon["headers"]))
    check("P5a Pages /api function forwards the tenant key too (same contract)",
          pages_api["headers"].get("x-platrixa-api-key") == TENANT, str(pages_api["headers"]))

    print("\nN — Next dev/preview proxy")
    nh = next_tenant["headers"]
    check("N1 caller tenant key forwarded UNCHANGED (operator key NOT substituted)",
          nh.get("x-platrixa-api-key") == TENANT, str(nh.get("x-platrixa-api-key")))
    check("N2 management token forwarded; idempotency-key survives",
          nh.get("x-platrixa-management-token") == MGMT and nh.get("idempotency-key") == IDEN,
          f"{nh.get('x-platrixa-management-token')}/{nh.get('idempotency-key')}")
    check("N3 absent caller key + operator key → documented fallback to operator key",
          next_fallback["headers"].get("x-platrixa-api-key") == "plx_dev_operator_shared_key",
          str(next_fallback["headers"].get("x-platrixa-api-key")))
    check("N4 absent caller key + absent operator key → NOTHING fabricated",
          "x-platrixa-api-key" not in next_none["headers"], str(next_none["headers"].get("x-platrixa-api-key")))
    mgmt_resp = data["nextResponses"]["mgmt"]
    check("N5 response contract headers pass back through the proxy",
          mgmt_resp.get("idempotent-replayed") == "true" and mgmt_resp.get("retry-after") == "2"
          and mgmt_resp.get("x-platrixa-error") == "QUOTA_EXHAUSTED", str(mgmt_resp))
    check("N6 backend URL used verbatim (fixed-target proxy preserved)",
          next_tenant["url"].startswith("https://api.example.com/v1/process")
          and pages_v1_tenant["url"].startswith("https://api.example.com/v1/process"),
          f"{next_tenant['url']} / {pages_v1_tenant['url']}")

    passed = sum(1 for _, ok, _ in CHECKS if ok)
    failed = [n for n, ok, _ in CHECKS if not ok]
    print("\n" + "=" * 78)
    print(f"Phase 5I proxy contract: {passed}/{len(CHECKS)} checks passed")
    if failed:
        print("FAILED:")
        for n in failed:
            print(f"  - {n}")
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
