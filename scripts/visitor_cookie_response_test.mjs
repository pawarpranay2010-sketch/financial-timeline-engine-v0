// Visitor cookie RESPONSE contract — all three proxies.
//
// Regression tests for the release blocker where the edge-owned
// `__Host-platrixa_visitor` cookie was written onto the OUTBOUND request
// headers instead of the response returned to the browser.
//
// Exercises the real modules:
//   - frontend/functions/api/[[path]].js   (Cloudflare Pages, /api)
//   - frontend/functions/v1/[[path]].js    (Cloudflare Pages, /v1)
//   - frontend/web/lib/platrixa-proxy.ts   (Next.js proxy, Node type stripping)
//
// Covered: missing / invalid / valid cookie, issuance attributes, persistence
// across successive requests, identity shared by /api and /v1, multiple
// upstream Set-Cookie preservation, upstream error responses, and streaming.
//
// Run: node scripts/visitor_cookie_response_test.mjs

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const ROOT = process.cwd();
const COOKIE = "__Host-platrixa_visitor";
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const ATTRS = "HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000";
const VALID_A = "3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e21";
const VALID_B = "7c9e4d2a-1b3f-4a5c-9d8e-2f6b0a1c3e57";
const PUBLIC_ORIGIN = "https://app.platrixa.test";
const BACKEND = "https://backend.platrixa.test";

const PAGES = [
  { key: "api", file: "frontend/functions/api/[[path]].js", base: "/api/v1/jobs/job_1" },
  { key: "v1", file: "frontend/functions/v1/[[path]].js", base: "/v1/jobs/job_1" },
];

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

// --- shared upstream stub ---------------------------------------------------
let lastUpstream = null;
let upstreamFactory = () =>
  new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });

globalThis.fetch = async (url, init) => {
  lastUpstream = {
    url: String(url),
    method: init?.method,
    headers: new Headers(init?.headers || {}),
    body: init?.body,
  };
  return upstreamFactory();
};

function setCookiesOf(response) {
  const headers = response.headers;
  if (typeof headers.getSetCookie === "function") return headers.getSetCookie();
  const single = headers.get("set-cookie");
  return single ? [single] : [];
}

function visitorCookieOf(response) {
  return setCookiesOf(response).find((c) => c.startsWith(`${COOKIE}=`)) || null;
}

function visitorValue(cookieHeader) {
  return cookieHeader.slice(COOKIE.length + 1).split(";")[0];
}

// --- Pages Functions (copied to .mjs so Node can import them verbatim) ------
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "platrixa-cookie-"));
async function loadPages(source) {
  const code = fs.readFileSync(path.join(ROOT, source.file), "utf8");
  const dest = path.join(tmp, `${source.key}.mjs`);
  fs.writeFileSync(dest, code);
  return import(pathToFileURL(dest).href);
}

function pagesContext(source, { method = "GET", headers = {}, env = {} } = {}) {
  const request = new Request(`${PUBLIC_ORIGIN}${source.base}`, {
    method,
    headers: new Headers(headers),
  });
  return {
    request,
    env: {
      API_BACKEND_URL: BACKEND,
      PLATRIXA_API_KEY: "server-side-website-key",
      PLATRIXA_PUBLIC_ORIGIN: PUBLIC_ORIGIN,
      PLATRIXA_VISITOR_HMAC_SECRET: "edge-shared-visitor-secret",
      ...env,
    },
  };
}

const pagesModules = {};
for (const source of PAGES) pagesModules[source.key] = await loadPages(source);

for (const source of PAGES) {
  const mod = pagesModules[source.key];
  const label = source.key;

  await test(`[${label}] no cookie → visitor cookie issued on the response`, async () => {
    lastUpstream = null;
    upstreamFactory = () =>
      new Response('{"ok":true}', {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    const res = await mod.onRequest(pagesContext(source));
    assert.equal(res.status, 200);
    const cookie = visitorCookieOf(res);
    assert.ok(cookie, "response must carry the visitor cookie");
    assert.equal(cookie, `${COOKIE}=${visitorValue(cookie)}; ${ATTRS}`);
    assert.match(visitorValue(cookie), UUID_V4);
    assert.ok(!/Domain=/i.test(cookie), "no Domain attribute (__Host- requirement)");
    assert.equal(lastUpstream.headers.get("set-cookie"), null, "cookie must not go upstream");
    assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), visitorValue(cookie));
  });

  await test(`[${label}] invalid cookie → replaced with a fresh uuid`, async () => {
    lastUpstream = null;
    const res = await mod.onRequest(
      pagesContext(source, { headers: { cookie: `${COOKIE}=not-a-uuid` } })
    );
    const cookie = visitorCookieOf(res);
    assert.ok(cookie, "an invalid cookie must be replaced");
    assert.match(visitorValue(cookie), UUID_V4);
    assert.notEqual(visitorValue(cookie), "not-a-uuid");
    assert.equal(lastUpstream.headers.get("set-cookie"), null);
  });

  await test(`[${label}] valid cookie → reused and NOT re-issued`, async () => {
    lastUpstream = null;
    const res = await mod.onRequest(
      pagesContext(source, { headers: { cookie: `${COOKIE}=${VALID_A}` } })
    );
    assert.equal(visitorCookieOf(res), null, "a valid cookie must not be re-issued");
    assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), VALID_A);
    assert.equal(lastUpstream.headers.get("set-cookie"), null);
  });

  await test(`[${label}] persistence across successive requests`, async () => {
    lastUpstream = null;
    const first = await mod.onRequest(pagesContext(source));
    const issued = visitorValue(visitorCookieOf(first));
    lastUpstream = null;
    const second = await mod.onRequest(
      pagesContext(source, { headers: { cookie: `${COOKIE}=${issued}` } })
    );
    assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), issued);
    assert.equal(visitorCookieOf(second), null, "the second request must not rotate identity");
  });

  await test(`[${label}] upstream Set-Cookie headers preserved alongside ours`, async () => {
    lastUpstream = null;
    upstreamFactory = () =>
      new Response('{"ok":true}', {
        status: 200,
        headers: [
          ["content-type", "application/json"],
          ["set-cookie", "upstream_a=1; Path=/"],
          ["set-cookie", "upstream_b=2; Path=/"],
        ],
      });
    const res = await mod.onRequest(pagesContext(source));
    const cookies = setCookiesOf(res);
    assert.equal(cookies.length, 3, `expected 3 Set-Cookie values, got ${cookies.length}`);
    assert.ok(cookies.includes("upstream_a=1; Path=/"), "upstream cookie a lost");
    assert.ok(cookies.includes("upstream_b=2; Path=/"), "upstream cookie b lost");
    assert.ok(cookies.some((c) => c.startsWith(`${COOKIE}=`)), "visitor cookie missing");
  });

  await test(`[${label}] upstream error status/body kept and cookie still issued`, async () => {
    lastUpstream = null;
    upstreamFactory = () =>
      new Response('{"api_version":"v1","error":{"code":"FAILED"}}', {
        status: 500,
        headers: { "content-type": "application/json" },
      });
    const res = await mod.onRequest(pagesContext(source));
    assert.equal(res.status, 500);
    assert.match(await res.text(), /FAILED/);
    assert.ok(visitorCookieOf(res), "cookie must be issued on error responses too");
  });

  await test(`[${label}] module parses as plain JavaScript`, async () => {
    assert.equal(typeof mod.onRequest, "function");
  });
}

// --- streaming (the /api proxy relays upstream.body) ------------------------
await test("[api] streaming body preserved", async () => {
  const encoder = new TextEncoder();
  upstreamFactory = () =>
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode('{"a":1'));
          controller.enqueue(encoder.encode("}"));
          controller.close();
        },
      }),
      { status: 200, headers: { "content-type": "application/json" } }
    );
  const res = await pagesModules.api.onRequest(pagesContext(PAGES[0]));
  assert.equal(await res.text(), '{"a":1}');
  assert.ok(visitorCookieOf(res));
});

// --- /api and /v1 share one visitor identity -------------------------------
await test("api and v1 accept the same visitor identity", async () => {
  lastUpstream = null;
  const issued = visitorValue(visitorCookieOf(await pagesModules.api.onRequest(pagesContext(PAGES[0]))));
  lastUpstream = null;
  const v1res = await pagesModules.v1.onRequest(
    pagesContext(PAGES[1], { headers: { cookie: `${COOKIE}=${issued}` } })
  );
  assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), issued);
  assert.equal(visitorCookieOf(v1res), null);
});

// --- Next.js proxy (real .ts module via Node type stripping) ---------------
process.env.PLATRIXA_API_BASE_URL = BACKEND;
process.env.PLATRIXA_API_KEY = "server-side-website-key";
process.env.PLATRIXA_PUBLIC_ORIGIN = PUBLIC_ORIGIN;
process.env.PLATRIXA_VISITOR_HMAC_SECRET = "edge-shared-visitor-secret";

const { forwardToPlatrixa, proxyResponse } = await import(
  "../frontend/web/lib/platrixa-proxy.ts"
);

function nextRequest({ method = "GET", headers = {} } = {}) {
  return {
    method,
    headers: new Headers(headers),
    nextUrl: { pathname: "/v1/jobs/job_1", search: "" },
    arrayBuffer: async () => new TextEncoder().encode("{}").buffer,
  };
}

async function callNext(opts) {
  const outcome = await forwardToPlatrixa(nextRequest(opts), [], "");
  return { outcome, response: proxyResponse(outcome) };
}

await test("[next] no cookie → issued on the response, never upstream", async () => {
  lastUpstream = null;
  upstreamFactory = () =>
    new Response('{"ok":true}', { status: 200, headers: { "content-type": "application/json" } });
  const { response } = await callNext({});
  const cookie = visitorCookieOf(response);
  assert.ok(cookie, "response must carry the visitor cookie");
  assert.equal(cookie, `${COOKIE}=${visitorValue(cookie)}; ${ATTRS}`);
  assert.match(visitorValue(cookie), UUID_V4);
  assert.equal(lastUpstream.headers.get("set-cookie"), null, "cookie must not go upstream");
  assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), visitorValue(cookie));
});

await test("[next] invalid cookie → replaced", async () => {
  lastUpstream = null;
  const { response } = await callNext({
    headers: { cookie: `${COOKIE}=0123456789abcdef` },
  });
  const cookie = visitorCookieOf(response);
  assert.ok(cookie);
  assert.match(visitorValue(cookie), UUID_V4);
  assert.equal(lastUpstream.headers.get("set-cookie"), null);
});

await test("[next] valid cookie reused, not re-issued; persists", async () => {
  lastUpstream = null;
  const first = await callNext({ headers: { cookie: `${COOKIE}=${VALID_B}` } });
  assert.equal(visitorCookieOf(first.response), null);
  assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), VALID_B);

  lastUpstream = null;
  const issued = visitorValue(visitorCookieOf((await callNext({})).response));
  const second = await callNext({ headers: { cookie: `${COOKIE}=${issued}` } });
  assert.equal(lastUpstream.headers.get("x-platrixa-visitor-id"), issued);
  assert.equal(visitorCookieOf(second.response), null);
});

await test("[next] upstream Set-Cookie values preserved alongside ours", async () => {
  upstreamFactory = () =>
    new Response('{"ok":true}', {
      status: 200,
      headers: [
        ["content-type", "application/json"],
        ["set-cookie", "upstream_a=1; Path=/"],
        ["set-cookie", "upstream_b=2; Path=/"],
      ],
    });
  const { response } = await callNext({});
  const cookies = setCookiesOf(response);
  assert.equal(cookies.length, 3, `expected 3 Set-Cookie values, got ${cookies.length}`);
  assert.ok(cookies.includes("upstream_a=1; Path=/"));
  assert.ok(cookies.includes("upstream_b=2; Path=/"));
});

await test("[next] upstream error status kept and cookie still issued", async () => {
  upstreamFactory = () =>
    new Response('{"api_version":"v1","error":{"code":"FAILED"}}', {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  const { response } = await callNext({});
  assert.equal(response.status, 500);
  assert.match(await response.text(), /FAILED/);
  assert.ok(visitorCookieOf(response));
  assert.equal(lastUpstream.headers.get("set-cookie"), null);
});

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} visitor-cookie response checks passed`);
process.exit(failed.length === 0 ? 0 : 1);
