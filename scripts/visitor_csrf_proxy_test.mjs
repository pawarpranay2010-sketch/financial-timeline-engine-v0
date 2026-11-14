// Visitor Ownership V2 — CSRF + proxy trust-boundary regression tests.
//
// Exercises the REAL Cloudflare Pages proxy modules (functions/api and
// functions/v1) under Node with a mocked upstream fetch:
//   - exact Origin validation for state-changing browser requests
//   - missing / null / malformed / foreign Origin rejection
//   - fail-closed when PLATRIXA_PUBLIC_ORIGIN is not configured
//   - GET/HEAD remain read-only (no body is forwarded)
//   - browser-supplied API key and visitor header are never trusted
//
// Run:  node scripts/visitor_csrf_proxy_test.mjs

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const ROOT = process.cwd();
const SOURCES = [
  { key: "api", file: "frontend/functions/api/[[path]].js", base: "/api/v1/jobs/job_1" },
  { key: "v1", file: "frontend/functions/v1/[[path]].js", base: "/v1/jobs/job_1" },
];

const PUBLIC_ORIGIN = "https://app.platrixa.test";
const BACKEND = "https://backend.platrixa.test";
const SERVER_KEY = "server-side-website-key";
const VISITOR_FROM_COOKIE = "0123456789abcdef0123456789abcdef";
const VISITOR_SECRET = "edge-shared-visitor-secret";

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "platrixa-proxy-"));

async function loadProxy(source) {
  const code = fs.readFileSync(path.join(ROOT, source.file), "utf8");
  const dest = path.join(tmp, `${source.key}.mjs`);
  fs.writeFileSync(dest, code);
  return import(pathToFileURL(dest).href);
}

let lastUpstream = null;
globalThis.fetch = async (url, init) => {
  lastUpstream = { url: String(url), method: init.method, headers: new Headers(init.headers), body: init.body };
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};

function context(source, { method = "GET", headers = {}, env = {} } = {}) {
  const requestHeaders = new Headers(headers);
  const hasBody = method !== "GET" && method !== "HEAD";
  const request = new Request(`https://app.platrixa.test${source.base}`, {
    method,
    headers: requestHeaders,
    body: hasBody ? "{}" : undefined,
  });
  return {
    request,
    env: {
      API_BACKEND_URL: BACKEND,
      PLATRIXA_API_KEY: SERVER_KEY,
      PLATRIXA_PUBLIC_ORIGIN: PUBLIC_ORIGIN,
      PLATRIXA_VISITOR_HMAC_SECRET: VISITOR_SECRET,
      ...env,
    },
  };
}

const results = [];
async function test(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`PASS  ${name}`);
  } catch (err) {
    results.push({ name, ok: false, error: err });
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}

for (const source of SOURCES) {
  const mod = await loadProxy(source);
  const label = source.key;

  // 10. Correct Origin is accepted for a mutation.
  await test(`[${label}] correct Origin accepted`, async () => {
    lastUpstream = null;
    const res = await mod.onRequest(context(source, {
      method: "POST",
      headers: { origin: PUBLIC_ORIGIN },
    }));
    assert.equal(res.status, 200);
    assert.ok(lastUpstream, "request should reach upstream");
  });

  // 11. Foreign Origin is rejected.
  await test(`[${label}] foreign Origin rejected`, async () => {
    lastUpstream = null;
    const res = await mod.onRequest(context(source, {
      method: "POST",
      headers: { origin: "https://evil.example" },
    }));
    assert.equal(res.status, 403);
    assert.equal(lastUpstream, null, "must not reach upstream");
  });

  // 12. Missing Origin is rejected for mutations.
  await test(`[${label}] missing Origin rejected`, async () => {
    const res = await mod.onRequest(context(source, { method: "POST" }));
    assert.equal(res.status, 403);
    assert.equal(lastUpstream, null);
  });

  // 13. "Origin: null" is rejected.
  await test(`[${label}] Origin: null rejected`, async () => {
    const res = await mod.onRequest(context(source, {
      method: "PUT",
      headers: { origin: "null" },
    }));
    assert.equal(res.status, 403);
  });

  // 14. Malformed Origin is rejected.
  await test(`[${label}] malformed Origin rejected`, async () => {
    const res = await mod.onRequest(context(source, {
      method: "DELETE",
      headers: { origin: "not a url" },
    }));
    assert.equal(res.status, 403);
  });

  // Fail closed when the public origin is not configured.
  await test(`[${label}] missing PLATRIXA_PUBLIC_ORIGIN fails closed`, async () => {
    const res = await mod.onRequest(context(source, {
      method: "POST",
      headers: { origin: PUBLIC_ORIGIN },
      env: { PLATRIXA_PUBLIC_ORIGIN: "" },
    }));
    assert.equal(res.status, 403);
  });

  // 16. Legitimate same-origin reads keep working (no Origin needed).
  await test(`[${label}] same-origin GET without Origin allowed`, async () => {
    lastUpstream = null;
    const res = await mod.onRequest(context(source, { method: "GET" }));
    assert.equal(res.status, 200);
    assert.ok(lastUpstream);
  });

  // 17. GET/HEAD are read-only: no request body is forwarded.
  await test(`[${label}] GET/HEAD forward no body`, async () => {
    for (const method of ["GET", "HEAD"]) {
      lastUpstream = null;
      await mod.onRequest(context(source, {
        method,
        headers: { origin: PUBLIC_ORIGIN },
      }));
      assert.equal(lastUpstream.body, undefined, `${method} must not carry a body`);
    }
  });

  // 18/19. Browser-supplied API key and visitor header are not trusted.
  await test(`[${label}] browser key + visitor header spoofing ignored`, async () => {
    lastUpstream = null;
    await mod.onRequest(context(source, {
      method: "GET",
      headers: {
        origin: PUBLIC_ORIGIN,
        "x-platrixa-api-key": "browser-forged-key",
        "x-platrixa-visitor-id": "spoofed-visitor-id-value",
        cookie: `platrixa_visitor_id=${VISITOR_FROM_COOKIE}`,
      },
    }));
    const sent = lastUpstream.headers;
    assert.equal(sent.get("x-platrixa-api-key"), SERVER_KEY);
    assert.equal(sent.get("x-platrixa-visitor-id"), VISITOR_FROM_COOKIE);
  });

  await test(`[${label}] edge signs a verifiable assertion + surface marker`, async () => {
    lastUpstream = null;
    await mod.onRequest(context(source, {
      method: "POST",
      headers: { origin: PUBLIC_ORIGIN },
    }));
    const h = lastUpstream.headers;
    assert.equal(h.get("x-platrixa-surface"), "website");
    const assertion = h.get("x-platrixa-visitor-assertion");
    assert.ok(assertion, "assertion must be present");
    const parts = assertion.split("|");
    assert.equal(parts.length, 10);
    assert.deepEqual(parts.slice(0, 2), ["V1", "platrixa"]);
    assert.equal(parts[2], h.get("x-platrixa-visitor-id"));
    assert.equal(parts[3], "POST");
    assert.equal(parts[4], source.base);
    assert.equal(parts[5], "");
    assert.equal(parts[6], crypto.createHash("sha256").update(Buffer.from("{}")).digest("hex"));
    assert.equal(parts[7], h.get("x-platrixa-request-timestamp"));
    assert.equal(parts[8], h.get("x-platrixa-visitor-nonce"));
    const expected = crypto
      .createHmac("sha256", VISITOR_SECRET)
      .update(parts.slice(0, 9).join("|"))
      .digest("hex");
    assert.equal(parts[9], expected, "signature must verify with the shared secret");
    assert.ok(Math.abs(Date.now() / 1000 - Number(parts[7])) <= 60);
  });

  await test(`[${label}] browser-supplied assertion/surface headers ignored`, async () => {
    lastUpstream = null;
    await mod.onRequest(context(source, {
      method: "GET",
      headers: {
        "x-platrixa-surface": "developer",
        "x-platrixa-visitor-assertion": "V1|platrixa|forged|GET|/||||||",
        "x-platrixa-request-timestamp": "1",
        "x-platrixa-visitor-nonce": "forged",
      },
    }));
    const h = lastUpstream.headers;
    assert.equal(h.get("x-platrixa-surface"), "website");
    assert.notEqual(h.get("x-platrixa-visitor-assertion"), "V1|platrixa|forged|GET|/||||||");
    assert.notEqual(h.get("x-platrixa-request-timestamp"), "1");
    assert.notEqual(h.get("x-platrixa-visitor-nonce"), "forged");
  });

  await test(`[${label}] missing signing secret fails closed`, async () => {
    lastUpstream = null;
    const res = await mod.onRequest(context(source, {
      method: "GET",
      env: { PLATRIXA_VISITOR_HMAC_SECRET: "" },
    }));
    assert.equal(res.status, 503);
    assert.equal(lastUpstream, null);
  });

  await test(`[${label}] no cookie → fresh id, spoofed header still ignored`, async () => {
    lastUpstream = null;
    await mod.onRequest(context(source, {
      method: "GET",
      headers: {
        "x-platrixa-visitor-id": "spoofed-visitor-id-value",
        cookie: "platrixa_visitor_id=short",
      },
    }));
    const sent = lastUpstream.headers.get("x-platrixa-visitor-id");
    assert.notEqual(sent, "spoofed-visitor-id-value");
    assert.match(sent, /^[A-Za-z0-9-]{16,64}$/);
  });
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} proxy tests passed`);
process.exit(failed.length === 0 ? 0 : 1);
