// Visitor Ownership V2 — CSRF regression tests for the Next.js dev/preview
// proxy (frontend/web/lib/platrixa-proxy.ts), executed with Node's built-in
// type stripping so the REAL module is exercised.
//
// Run:  node scripts/visitor_csrf_next_proxy_test.mjs

import assert from "node:assert/strict";
import crypto from "node:crypto";

const PUBLIC_ORIGIN = "https://app.platrixa.test";
const BACKEND = "https://backend.platrixa.test";
const SERVER_KEY = "server-side-website-key";
const COOKIE_VISITOR = "0123456789abcdef0123456789abcdef";
const VISITOR_SECRET = "edge-shared-visitor-secret";

process.env.PLATRIXA_API_BASE_URL = BACKEND;
process.env.PLATRIXA_API_KEY = SERVER_KEY;
process.env.PLATRIXA_PUBLIC_ORIGIN = PUBLIC_ORIGIN;
process.env.PLATRIXA_VISITOR_HMAC_SECRET = VISITOR_SECRET;

const { forwardToPlatrixa, proxyResponse } = await import(
  "../frontend/web/lib/platrixa-proxy.ts"
);

let lastUpstream = null;
globalThis.fetch = async (url, init) => {
  lastUpstream = { url: String(url), headers: new Headers(init.headers), body: init.body };
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};

function makeRequest({ method = "POST", path = "/v1/jobs/job_1", headers = {} } = {}) {
  return {
    method,
    headers: new Headers(headers),
    nextUrl: { pathname: path, search: "" },
    arrayBuffer: async () => new TextEncoder().encode("{}").buffer,
  };
}

async function call(opts) {
  const outcome = await forwardToPlatrixa(makeRequest(opts), [], "");
  return { outcome, response: proxyResponse(outcome) };
}

const results = [];
async function test(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
    console.log(`PASS  ${name}`);
  } catch (err) {
    results.push({ name, ok: false });
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}

await test("correct Origin accepted for mutation", async () => {
  lastUpstream = null;
  const { response } = await call({ method: "POST", headers: { origin: PUBLIC_ORIGIN } });
  assert.equal(response.status, 200);
  assert.ok(lastUpstream);
});

await test("foreign Origin rejected", async () => {
  lastUpstream = null;
  const { response } = await call({ method: "POST", headers: { origin: "https://evil.example" } });
  assert.equal(response.status, 403);
  assert.equal(lastUpstream, null);
});

await test("missing Origin rejected", async () => {
  const { response } = await call({ method: "POST" });
  assert.equal(response.status, 403);
});

await test("Origin: null rejected", async () => {
  const { response } = await call({ method: "PUT", headers: { origin: "null" } });
  assert.equal(response.status, 403);
});

await test("malformed Origin rejected", async () => {
  const { response } = await call({ method: "DELETE", headers: { origin: "not a url" } });
  assert.equal(response.status, 403);
});

await test("missing PLATRIXA_PUBLIC_ORIGIN fails closed", async () => {
  const saved = process.env.PLATRIXA_PUBLIC_ORIGIN;
  process.env.PLATRIXA_PUBLIC_ORIGIN = "";
  try {
    const { response } = await call({ method: "POST", headers: { origin: PUBLIC_ORIGIN } });
    assert.equal(response.status, 403);
  } finally {
    process.env.PLATRIXA_PUBLIC_ORIGIN = saved;
  }
});

await test("same-origin GET without Origin allowed", async () => {
  lastUpstream = null;
  const { response } = await call({ method: "GET" });
  assert.equal(response.status, 200);
  assert.ok(lastUpstream);
});

await test("GET/HEAD forward no body", async () => {
  for (const method of ["GET", "HEAD"]) {
    lastUpstream = null;
    await call({ method });
    assert.equal(lastUpstream.body, undefined);
  }
});

await test("browser key + visitor header spoofing ignored", async () => {
  lastUpstream = null;
  await call({
    method: "GET",
    headers: {
      "x-platrixa-api-key": "browser-forged-key",
      "x-platrixa-visitor-id": "spoofed-visitor-id-value",
      cookie: `platrixa_visitor_id=${COOKIE_VISITOR}`,
    },
  });
  assert.equal(lastUpstream.headers.get("x-platrixa-api-key"), SERVER_KEY);
  assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), COOKIE_VISITOR);
});

await test("edge signs a verifiable assertion + surface marker", async () => {
  lastUpstream = null;
  await call({ method: "POST", headers: { origin: PUBLIC_ORIGIN } });
  const h = lastUpstream.headers;
  assert.equal(h.get("x-platrixa-surface"), "website");
  const parts = h.get("x-platrixa-visitor-assertion").split("|");
  assert.equal(parts.length, 10);
  assert.equal(parts[2], h.get("x-platrixa-visitor-id"));
  assert.equal(parts[3], "POST");
  assert.equal(parts[4], "/v1/jobs/job_1");
  assert.equal(parts[6], crypto.createHash("sha256").update(Buffer.from("{}")).digest("hex"));
  const expected = crypto
    .createHmac("sha256", VISITOR_SECRET)
    .update(parts.slice(0, 9).join("|"))
    .digest("hex");
  assert.equal(parts[9], expected);
});

await test("browser-supplied assertion/surface headers ignored", async () => {
  lastUpstream = null;
  await call({
    method: "GET",
    headers: {
      "x-platrixa-surface": "developer",
      "x-platrixa-visitor-assertion": "V1|platrixa|forged|GET|/|",
      "x-platrixa-request-timestamp": "1",
      "x-platrixa-visitor-nonce": "forged",
    },
  });
  const h = lastUpstream.headers;
  assert.equal(h.get("x-platrixa-surface"), "website");
  assert.notEqual(h.get("x-platrixa-visitor-assertion"), "V1|platrixa|forged|GET|/|");
  assert.notEqual(h.get("x-platrixa-request-timestamp"), "1");
});

await test("missing signing secret fails closed", async () => {
  lastUpstream = null;
  const saved = process.env.PLATRIXA_VISITOR_HMAC_SECRET;
  process.env.PLATRIXA_VISITOR_HMAC_SECRET = "";
  try {
    const { response } = await call({ method: "GET" });
    assert.equal(response.status, 503);
    assert.equal(lastUpstream, null);
  } finally {
    process.env.PLATRIXA_VISITOR_HMAC_SECRET = saved;
  }
});

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} Next proxy tests passed`);
process.exit(failed.length === 0 ? 0 : 1);
