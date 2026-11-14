// Visitor Ownership V2 — proxy → backend integration over real HTTP.
//
// Exercises the REAL Cloudflare Pages proxy modules against a REAL running
// FastAPI backend (no mocked fetch, no mocked verification):
//   browser cookie → edge identity → edge HMAC signature → backend verification
//   → ownership predicate → HTTP response.
//
// Run:
//   V2_BACKEND_URL=http://127.0.0.1:8011 \
//   V2_TEST_API_KEY=<key in the isolated store> \
//   PLATRIXA_PUBLIC_ORIGIN=https://app.platrixa.test \
//   PLATRIXA_VISITOR_HMAC_SECRET=<same as backend> \
//       node scripts/v2_proxy_integration_test.mjs

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const BACKEND = process.env.V2_BACKEND_URL || "http://127.0.0.1:8011";
const API_KEY = process.env.V2_TEST_API_KEY || "";
const PUBLIC_ORIGIN = process.env.PLATRIXA_PUBLIC_ORIGIN || "https://app.platrixa.test";
const VISITOR_SECRET = process.env.PLATRIXA_VISITOR_HMAC_SECRET || "";

if (!API_KEY || !VISITOR_SECRET) {
  console.error("V2_TEST_API_KEY and PLATRIXA_VISITOR_HMAC_SECRET are required");
  process.exit(2);
}

const A = "a".repeat(32);
const B = "b".repeat(32);

const ROOT = process.cwd();
const SOURCES = [
  { key: "api", file: "frontend/functions/api/[[path]].js" },
  { key: "v1", file: "frontend/functions/v1/[[path]].js" },
];

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "v2-proxy-int-"));
async function load(source) {
  const dest = path.join(tmp, `${source.key}.mjs`);
  fs.writeFileSync(dest, fs.readFileSync(path.join(ROOT, source.file), "utf8"));
  return import(pathToFileURL(dest).href);
}

function ctx(source, { method = "GET", pathname, headers = {} } = {}) {
  return {
    request: new Request(`https://app.platrixa.test${pathname}`, {
      method,
      headers: new Headers(headers),
      body: method === "GET" || method === "HEAD" ? undefined : "{}",
    }),
    env: {
      API_BACKEND_URL: BACKEND,
      PLATRIXA_API_KEY: API_KEY,
      PLATRIXA_PUBLIC_ORIGIN: PUBLIC_ORIGIN,
      PLATRIXA_VISITOR_HMAC_SECRET: VISITOR_SECRET,
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
    results.push({ name, ok: false });
    console.log(`FAIL  ${name}\n      ${err.message}`);
  }
}

const cookie = (visitor) => ({ cookie: `platrixa_visitor_id=${visitor}` });

// Job/result routes live under /v1 only; the /api namespace is exercised
// separately below as a pass-through pipeline check.
for (const source of SOURCES.filter((s) => s.key === "v1")) {
  const mod = await load(source);
  const label = source.key === "api" ? "/api" : "/v1";
  const jobsPath = (id) => `${label === "/api" ? "/api" : ""}/v1/jobs/${id}`;

  await test(`[${label}] A reads own job through proxy+backend`, async () => {
    const res = await mod.onRequest(ctx(source, { pathname: jobsPath("job_a"), headers: cookie(A) }));
    assert.equal(res.status, 200, `status ${res.status}: ${await res.text()}`);
  });

  await test(`[${label}] B cannot read A's job through proxy+backend`, async () => {
    const res = await mod.onRequest(ctx(source, { pathname: jobsPath("job_a"), headers: cookie(B) }));
    assert.equal(res.status, 404, `status ${res.status}`);
  });

  await test(`[${label}] spoofed browser visitor header ignored (cookie wins)`, async () => {
    const res = await mod.onRequest(ctx(source, {
      pathname: jobsPath("job_a"),
      headers: { ...cookie(B), "x-platrixa-visitor-id": A },
    }));
    assert.equal(res.status, 404, `spoof changed the outcome: ${res.status}`);
  });

  await test(`[${label}] spoofed browser assertion ignored (edge re-signs)`, async () => {
    const res = await mod.onRequest(ctx(source, {
      pathname: jobsPath("job_a"),
      headers: {
        ...cookie(B),
        "x-platrixa-visitor-assertion": "V1|platrixa|forged|GET|/v1/jobs/job_a||e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|9999999999|forged|00",
        "x-platrixa-surface": "developer",
      },
    }));
    assert.equal(res.status, 404, `forged assertion changed the outcome: ${res.status}`);
  });

  await test(`[${label}] website visitor cannot reach developer job`, async () => {
    const res = await mod.onRequest(ctx(source, { pathname: jobsPath("job_dev"), headers: cookie(A) }));
    assert.equal(res.status, 404, `status ${res.status}`);
  });

  await test(`[${label}] website visitor cannot reach legacy job`, async () => {
    const res = await mod.onRequest(ctx(source, { pathname: jobsPath("job_legacy"), headers: cookie(A) }));
    assert.equal(res.status, 404, `status ${res.status}`);
  });

  await test(`[${label}] request target is part of the signed assertion`, async () => {
    const res = await mod.onRequest(ctx(source, { pathname: `${jobsPath("job_a")}?trace=1`, headers: cookie(A) }));
    assert.equal(res.status, 200, `status ${res.status}`);
  });

  await test(`[${label}] proxied requests are always website surface`, async () => {
    // Developer clients do not traverse the website proxy; a proxied request
    // therefore never carries developer authority, even for a developer job.
    const res = await mod.onRequest(ctx(source, { pathname: jobsPath("job_dev") }));
    assert.equal(res.status, 404, `status ${res.status}`);
  });
}

// /api namespace pipeline sanity (identity + signing + CSRF mechanics).
{
  const mod = await load(SOURCES[0]);
  await test("[/api] health reachable through proxy with edge identity", async () => {
    const res = await mod.onRequest(ctx(SOURCES[0], { pathname: "/api/v1/health", headers: cookie(A) }));
    assert.equal(res.status, 200, `status ${res.status}`);
  });
  await test("[/api] mutation with foreign Origin rejected", async () => {
    const res = await mod.onRequest(ctx(SOURCES[0], {
      method: "POST",
      pathname: "/api/v1/health",
      headers: { ...cookie(A), origin: "https://evil.example" },
    }));
    assert.equal(res.status, 403, `status ${res.status}`);
  });
  await test("[/api] mutation with trusted Origin passes the gate", async () => {
    const res = await mod.onRequest(ctx(SOURCES[0], {
      method: "POST",
      pathname: "/api/v1/health",
      headers: { ...cookie(A), origin: PUBLIC_ORIGIN },
    }));
    assert.equal(res.status, 405, `status ${res.status}`);
  });
}

// Origin gate is enforced end to end (proxy rejects before forwarding)
{
  const mod = await load(SOURCES[1]);
  await test("mutation with trusted Origin reaches the backend", async () => {
    const res = await mod.onRequest(ctx(SOURCES[1], {
      method: "POST",
      pathname: "/v1/jobs/job_a",
      headers: { ...cookie(A), origin: PUBLIC_ORIGIN },
    }));
    // Route is GET-only → 405 proves the request passed CSRF and backend auth.
    assert.equal(res.status, 405, `status ${res.status}`);
  });
  await test("mutation with foreign Origin rejected before backend", async () => {
    const res = await mod.onRequest(ctx(SOURCES[1], {
      method: "POST",
      pathname: "/v1/jobs/job_a",
      headers: { ...cookie(A), origin: "https://evil.example" },
    }));
    assert.equal(res.status, 403, `status ${res.status}`);
  });
}

// Developer authorization is proven directly against the backend (no proxy).
await test("developer client reads developer job directly (no surface)", async () => {
  const res = await fetch(`${BACKEND}/v1/jobs/job_dev`, {
    headers: { "x-platrixa-api-key": API_KEY },
  });
  assert.equal(res.status, 200, `status ${res.status}: ${await res.text()}`);
});

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} integration checks passed`);
process.exit(failed.length === 0 ? 0 : 1);
