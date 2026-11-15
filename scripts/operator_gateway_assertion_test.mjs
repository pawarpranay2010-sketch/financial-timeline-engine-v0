// Isolated tests for the operator Pages Function assertion signer.
//
// Executes frontend/functions/operator/[[path]].js directly in Node (Web Crypto,
// Request/Response/Headers/URL, AbortSignal.timeout are all available), stubs
// the outbound fetch, and verifies every forwarded request:
//
//   • all 11 assertion headers are present on all 8 management endpoints
//   • the HMAC verifies over the ACTUAL forwarded method/path/query/
//     content-type/body using an independent implementation (node:crypto)
//   • the declared values equal the actual transport values
//   • the body hash is SHA-256 of the exact forwarded bytes
//   • nonce/timestamp/issuer/audience satisfy the backend's rules
//   • caller-supplied assertion, tenant and operator headers cannot override
//     the generated ones
//   • missing configuration fails closed without any backend call
//   • responses keep status, contract headers and streamed body
//
// It also writes a fixture of the eight signed requests so the Python
// compatibility test can replay them against the real backend dependency.
//
// Run: node scripts/operator_gateway_assertion_test.mjs

import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import handler from "../frontend/functions/operator/[[path]].js";

const RESULTS = [];
function check(name, condition, detail = "") {
  RESULTS.push([name, !!condition, detail]);
  if (!condition) {
    console.error(`  [FAIL] ${name}${detail ? ` — ${detail}` : ""}`);
  } else {
    console.log(`  [PASS] ${name}`);
  }
}

const ENV = {
  PLATRIXA_MANAGEMENT_ASSERTION_SECRET: "  operator-assertion-secret  ",
  PLATRIXA_MANAGEMENT_ASSERTION_ISSUER: "platrixa-operator-v1",
  PLATRIXA_KEY_MANAGEMENT_TOKEN: "mgmt-token-under-test",
  PLATRIXA_KEY_MANAGEMENT_TENANT_ID: "tenant-under-test",
  API_BACKEND_URL: "https://backend.example",
};
const SIGNING_SECRET = ENV.PLATRIXA_MANAGEMENT_ASSERTION_SECRET.trim();
const BACKEND_ORIGIN = "https://backend.example";

const ASSERTION_HEADERS = [
  "x-platrixa-assertion-signature",
  "x-platrixa-assertion-issuer",
  "x-platrixa-assertion-audience",
  "x-platrixa-assertion-tenant-id",
  "x-platrixa-assertion-method",
  "x-platrixa-assertion-path",
  "x-platrixa-assertion-query-string",
  "x-platrixa-assertion-content-type",
  "x-platrixa-assertion-body-hash",
  "x-platrixa-assertion-timestamp",
  "x-platrixa-assertion-nonce",
];

let CALLS = [];
let upstreamResponse = () =>
  new Response('{"ok":true}', {
    status: 200,
    headers: { "content-type": "application/json", "x-request-id": "rid-1" },
  });

globalThis.fetch = async (url, init) => {
  const headers = {};
  for (const [k, v] of new Headers(init?.headers || {}).entries()) headers[k] = v;
  const body = init?.body ? Buffer.from(init.body) : Buffer.alloc(0);
  CALLS.push({
    url: String(url),
    method: init?.method,
    headers,
    body,
    signalPresent: !!init?.signal,
    redirect: init?.redirect,
  });
  return upstreamResponse();
};

function reset() {
  CALLS = [];
  upstreamResponse = () =>
    new Response('{"ok":true}', {
      status: 200,
      headers: { "content-type": "application/json", "x-request-id": "rid-1" },
    });
}

async function call(pathWithQuery, { method = "GET", body, headers = {}, env = ENV } = {}) {
  const init = { method, headers: { ...headers } };
  if (body !== undefined && method !== "GET" && method !== "HEAD") {
    // A real Pages request arrives with the caller's content-type; the
    // Request constructor would otherwise default a string body to text/plain.
    if (init.headers["content-type"] === undefined) {
      init.headers["content-type"] = "application/json";
    }
    init.body = body;
  }
  const request = new Request(`https://operator.example${pathWithQuery}`, init);
  const response = await handler.fetch(request, env, {});
  return { response, last: CALLS[CALLS.length - 1], count: CALLS.length };
}

const sha256 = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");

/** Verifies one captured upstream call the way the backend does. */
function verifyCaptured(call, { expectMethod, expectPath, expectQuery, label }) {
  const h = call.headers;
  for (const name of ASSERTION_HEADERS) {
    // Presence, not emptiness: an empty query string is a legitimate value.
    assert.ok(h[name] !== undefined, `missing ${name} (${label})`);
  }

  const url = new URL(call.url);
  assert.equal(url.origin, BACKEND_ORIGIN, `${label}: wrong origin ${url.origin}`);
  assert.equal(call.method, expectMethod, `${label}: forwarded method`);
  assert.equal(url.pathname, expectPath, `${label}: forwarded path`);
  assert.equal(url.search.replace(/^\?/, ""), expectQuery, `${label}: forwarded query`);

  // Declared values must equal the actual transport values.
  assert.equal(h["x-platrixa-assertion-method"], call.method, `${label}: method binding`);
  assert.equal(h["x-platrixa-assertion-path"], url.pathname, `${label}: path binding`);
  assert.equal(
    h["x-platrixa-assertion-query-string"],
    url.search.replace(/^\?/, ""),
    `${label}: query binding`
  );
  assert.equal(
    h["x-platrixa-assertion-content-type"],
    h["content-type"],
    `${label}: content-type binding`
  );
  assert.equal(
    h["x-platrixa-assertion-body-hash"],
    sha256(call.body),
    `${label}: body hash binding`
  );

  // Backend rules.
  assert.equal(h["x-platrixa-assertion-audience"], "platrixa-management", `${label}: audience`);
  assert.match(h["x-platrixa-assertion-issuer"], /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
  assert.match(h["x-platrixa-assertion-nonce"], /^[A-Za-z0-9._:-]{16,128}$/);
  const skew = Math.abs(Math.floor(Date.now() / 1000) - Number(h["x-platrixa-assertion-timestamp"]));
  assert.ok(skew <= 300, `${label}: timestamp skew ${skew}`);
  assert.equal(h["x-platrixa-assertion-tenant-id"], ENV.PLATRIXA_KEY_MANAGEMENT_TENANT_ID);

  // Independent HMAC over the 10-field canonical message.
  const canonical = [
    h["x-platrixa-assertion-issuer"],
    h["x-platrixa-assertion-audience"],
    h["x-platrixa-assertion-tenant-id"],
    h["x-platrixa-assertion-method"],
    h["x-platrixa-assertion-path"],
    h["x-platrixa-assertion-query-string"],
    h["x-platrixa-assertion-content-type"],
    h["x-platrixa-assertion-body-hash"],
    h["x-platrixa-assertion-timestamp"],
    h["x-platrixa-assertion-nonce"],
  ].join("|");
  const expected = crypto.createHmac("sha256", SIGNING_SECRET).update(canonical).digest("hex");
  assert.equal(h["x-platrixa-assertion-signature"], expected, `${label}: signature mismatch`);

  // The server-side token is the only one forwarded.
  assert.equal(h["x-platrixa-management-token"], ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN);
}

// ---------------------------------------------------------------------------
// 1. Every management endpoint receives a complete, correct assertion
// ---------------------------------------------------------------------------

const KEY_ID = "11111111-2222-3333-4444-555555555555";
const ENDPOINTS = [
  { name: "create key", method: "POST", path: "/v1/developer/api-keys", body: '{"name":"k","environment":"test"}' },
  { name: "list keys", method: "GET", path: "/v1/developer/api-keys" },
  { name: "revoke key", method: "POST", path: `/v1/developer/api-keys/${KEY_ID}/revoke` },
  { name: "rotate key", method: "POST", path: `/v1/developer/api-keys/${KEY_ID}/rotate` },
  { name: "key usage", method: "GET", path: `/v1/developer/api-keys/${KEY_ID}/usage` },
  { name: "tenant usage", method: "GET", path: "/v1/developer/usage" },
  { name: "request list", method: "GET", path: "/v1/developer/requests" },
  { name: "request detail", method: "GET", path: `/v1/developer/requests/${KEY_ID}` },
];

const FIXTURE = [];

for (const endpoint of ENDPOINTS) {
  reset();
  const { response, last, count } = await call(endpoint.path, {
    method: endpoint.method,
    body: endpoint.body,
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  let ok = true;
  let detail = "";
  try {
    assert.equal(count, 1, "exactly one backend call");
    assert.equal(response.status, 200);
    verifyCaptured(last, {
      expectMethod: endpoint.method,
      expectPath: endpoint.path,
      expectQuery: "",
      label: endpoint.name,
    });
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check(`signed + bound request for ${endpoint.method} ${endpoint.path}`, ok, detail);

  FIXTURE.push({
    name: endpoint.name,
    method: endpoint.method,
    path_with_query: new URL(last.url).pathname + new URL(last.url).search,
    headers: last.headers,
    body_b64: last.body.toString("base64"),
    expected_method: endpoint.method,
  });
}

// ---------------------------------------------------------------------------
// 2. Query string and percent-encoded path are signed verbatim
// ---------------------------------------------------------------------------

reset();
{
  const { last } = await call("/v1/developer/requests?limit=5&cursor=a%2Fb", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  let ok = true;
  let detail = "";
  try {
    assert.equal(last.headers["x-platrixa-assertion-query-string"], "limit=5&cursor=a%2Fb");
    assert.equal(last.headers["x-platrixa-assertion-path"], "/v1/developer/requests");
    verifyCaptured(last, {
      expectMethod: "GET",
      expectPath: "/v1/developer/requests",
      expectQuery: "limit=5&cursor=a%2Fb",
      label: "query",
    });
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check("raw query string is signed and forwarded verbatim", ok, detail);
}

reset();
{
  const encodedPath = `/v1/developer/requests/${KEY_ID}%2Fsuffix`;
  const { last } = await call(encodedPath, {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  let ok = true;
  let detail = "";
  try {
    // %2F is preserved, never decoded into a path separator.
    assert.equal(last.headers["x-platrixa-assertion-path"], encodedPath);
    verifyCaptured(last, {
      expectMethod: "GET",
      expectPath: encodedPath,
      expectQuery: "",
      label: "percent-encoding",
    });
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check("percent-encoded path is preserved in the signed path", ok, detail);
}

// ---------------------------------------------------------------------------
// 3. Empty body on POST signs SHA-256 of empty bytes
// ---------------------------------------------------------------------------

reset();
{
  const { last } = await call(`/v1/developer/api-keys/${KEY_ID}/revoke`, {
    method: "POST",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  let ok = true;
  let detail = "";
  try {
    assert.equal(last.headers["x-platrixa-assertion-body-hash"], sha256(Buffer.alloc(0)));
    assert.equal(last.headers["x-platrixa-assertion-body-hash"], "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    verifyCaptured(last, {
      expectMethod: "POST",
      expectPath: `/v1/developer/api-keys/${KEY_ID}/revoke`,
      expectQuery: "",
      label: "empty body",
    });
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check("empty POST body uses SHA-256 of empty bytes", ok, detail);
}

// ---------------------------------------------------------------------------
// 4. Binding: a signature is not transferable to another method/path/query/body
// ---------------------------------------------------------------------------

reset();
{
  const first = await call("/v1/developer/api-keys", {
    method: "POST",
    body: '{"name":"one"}',
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const second = await call("/v1/developer/api-keys", {
    method: "POST",
    body: '{"name":"two"}',
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const a = first.last;
  const b = second.last;
  let ok = true;
  let detail = "";
  try {
    assert.notEqual(a.headers["x-platrixa-assertion-signature"], b.headers["x-platrixa-assertion-signature"]);
    assert.notEqual(a.headers["x-platrixa-assertion-nonce"], b.headers["x-platrixa-assertion-nonce"]);
    // Signing A's message must not verify for B's body hash.
    const forgedForB = crypto
      .createHmac("sha256", SIGNING_SECRET)
      .update(
        [
          a.headers["x-platrixa-assertion-issuer"],
          a.headers["x-platrixa-assertion-audience"],
          a.headers["x-platrixa-assertion-tenant-id"],
          a.headers["x-platrixa-assertion-method"],
          a.headers["x-platrixa-assertion-path"],
          a.headers["x-platrixa-assertion-query-string"],
          a.headers["x-platrixa-assertion-content-type"],
          b.headers["x-platrixa-assertion-body-hash"],
          a.headers["x-platrixa-assertion-timestamp"],
          a.headers["x-platrixa-assertion-nonce"],
        ].join("|")
      )
      .digest("hex");
    assert.notEqual(forgedForB, b.headers["x-platrixa-assertion-signature"]);
    assert.notEqual(a.headers["x-platrixa-assertion-body-hash"], b.headers["x-platrixa-assertion-body-hash"]);
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check("altered body changes the signed body hash and signature", ok, detail);
}

// ---------------------------------------------------------------------------
// 5. Browser-supplied assertion / tenant / operator headers are discarded
// ---------------------------------------------------------------------------

reset();
{
  const forged = {
    "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN,
    "x-platrixa-assertion-signature": "f".repeat(64),
    "x-platrixa-assertion-issuer": "attacker-issuer",
    "x-platrixa-assertion-audience": "attacker-audience",
    "x-platrixa-assertion-tenant-id": "attacker-tenant",
    "x-platrixa-assertion-method": "DELETE",
    "x-platrixa-assertion-path": "/v1/developer/other",
    "x-platrixa-assertion-query-string": "a=b",
    "x-platrixa-assertion-content-type": "text/plain",
    "x-platrixa-assertion-body-hash": "0".repeat(64),
    "x-platrixa-assertion-timestamp": "1",
    "x-platrixa-assertion-nonce": "attacker-nonce-0001",
    "x-platrixa-tenant-id": "attacker-tenant",
    "x-platrixa-operator-id": "attacker",
  };
  const { last } = await call("/v1/developer/usage", {
    method: "GET",
    headers: forged,
  });
  let ok = true;
  let detail = "";
  try {
    verifyCaptured(last, {
      expectMethod: "GET",
      expectPath: "/v1/developer/usage",
      expectQuery: "",
      label: "spoofing",
    });
    assert.notEqual(last.headers["x-platrixa-assertion-signature"], forged["x-platrixa-assertion-signature"]);
    assert.notEqual(last.headers["x-platrixa-assertion-issuer"], "attacker-issuer");
    assert.notEqual(last.headers["x-platrixa-assertion-tenant-id"], "attacker-tenant");
    assert.equal(last.headers["x-platrixa-tenant-id"], ENV.PLATRIXA_KEY_MANAGEMENT_TENANT_ID);
    assert.equal(last.headers["x-platrixa-operator-id"], undefined);
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check("browser assertion/tenant/operator headers cannot override generated ones", ok, detail);
}

// A wrong caller token is still rejected before any signing or forwarding.
reset();
{
  const { response, count } = await call("/v1/developer/api-keys", {
    method: "GET",
    headers: { "x-platrixa-management-token": "wrong-token" },
  });
  check(
    "wrong caller management token rejected (401, no backend call)",
    response.status === 401 && count === 0,
    `${response.status}/${count}`
  );
}
reset();
{
  const { response, count } = await call("/v1/developer/api-keys", { method: "GET" });
  check(
    "missing caller management token rejected (401, no backend call)",
    response.status === 401 && count === 0,
    `${response.status}/${count}`
  );
}

// ---------------------------------------------------------------------------
// 6. Missing configuration fails closed, with no backend call
// ---------------------------------------------------------------------------

const CONFIG_CASES = [
  ["PLATRIXA_MANAGEMENT_ASSERTION_SECRET", "MANAGEMENT_ASSERTION_SECRET_MISSING", 500],
  ["PLATRIXA_MANAGEMENT_ASSERTION_ISSUER", "MANAGEMENT_ASSERTION_ISSUER_INVALID", 500],
  ["PLATRIXA_KEY_MANAGEMENT_TENANT_ID", "OPERATOR_UNAUTHENTICATED", 401],
  ["PLATRIXA_KEY_MANAGEMENT_TOKEN", "OPERATOR_UNAUTHENTICATED", 401],
  ["API_BACKEND_URL", "BACKEND_NOT_CONFIGURED", 500],
];

for (const [key, code, status] of CONFIG_CASES) {
  reset();
  const env = { ...ENV };
  delete env[key];
  const { response, count } = await call("/v1/developer/api-keys", {
    method: "GET",
    env,
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  let body = {};
  try {
    body = await response.json();
  } catch (_err) {
    body = {};
  }
  check(
    `missing ${key} fails closed (${status} ${code}, no forward)`,
    response.status === status && body?.error?.code === code && count === 0,
    `${response.status} ${body?.error?.code}/${count}`
  );
}

// Malformed issuer is rejected too.
reset();
{
  const { response, count } = await call("/v1/developer/api-keys", {
    method: "GET",
    env: { ...ENV, PLATRIXA_MANAGEMENT_ASSERTION_ISSUER: "bad issuer|with|pipes" },
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const body = await response.json();
  check(
    "malformed issuer fails closed",
    response.status === 500 && body?.error?.code === "MANAGEMENT_ASSERTION_ISSUER_INVALID" && count === 0,
    `${response.status}/${count}`
  );
}

// ---------------------------------------------------------------------------
// 7. Routing stays allowlisted
// ---------------------------------------------------------------------------

const ROUTE_CASES = [
  ["GET", "/v1/operator/manage", 404],
  ["GET", "/v1/developer/api-keys/abc", 404],
  ["POST", "/v1/developer/requests", 404],
  ["GET", "/v1/process", 404],
  ["GET", "//evil.example/v1/developer/usage", 404],
];

for (const [method, path, status] of ROUTE_CASES) {
  reset();
  const { response, count } = await call(path, {
    method,
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  check(
    `${method} ${path} → ${status} (no backend call)`,
    response.status === status && count === 0,
    `${response.status}/${count}`
  );
}

// Pages mounts this function at /operator/* (DEPLOYMENT.md: frontend/functions
// is copied into the build output as functions/). The mount prefix must be
// stripped before signing so the signed path equals the forwarded path.
reset();
{
  const { response, last } = await call("/operator/v1/developer/usage?window=7", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  let ok = true;
  let detail = "";
  try {
    assert.equal(response.status, 200);
    assert.equal(new URL(last.url).pathname, "/v1/developer/usage", "prefix must be stripped");
    verifyCaptured(last, {
      expectMethod: "GET",
      expectPath: "/v1/developer/usage",
      expectQuery: "window=7",
      label: "operator mount",
    });
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check("/operator/v1/developer/usage is signed and forwarded as /v1/developer/usage", ok, detail);
}

reset();
{
  const { response, count } = await call("/operator", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  check("/operator without a management path → 404 (no forward)", response.status === 404 && count === 0, `${response.status}/${count}`);
}

// Legacy operator entry point is still forwarded.
reset();
{
  const { response, last } = await call("/v1/operator/manage", {
    method: "POST",
    body: '{"op":"x"}',
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  let ok = true;
  let detail = "";
  try {
    assert.equal(response.status, 200);
    assert.equal(new URL(last.url).pathname, "/v1/operator/manage");
    verifyCaptured(last, {
      expectMethod: "POST",
      expectPath: "/v1/operator/manage",
      expectQuery: "",
      label: "legacy",
    });
  } catch (err) {
    ok = false;
    detail = err.message;
  }
  check("legacy POST /v1/operator/manage still forwarded", ok, detail);
}

// ---------------------------------------------------------------------------
// 8. Response status, headers and streaming body are preserved
// ---------------------------------------------------------------------------

reset();
upstreamResponse = () =>
  new Response("streamed-body", {
    status: 429,
    headers: {
      "content-type": "application/json",
      "retry-after": "7",
      "x-ratelimit-limit": "10",
      "x-platrixa-error": "RATE_LIMITED",
      "x-request-id": "rid-9",
      "x-internal-debug": "must-not-leak",
    },
  });
{
  const { response } = await call("/v1/developer/api-keys", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const text = await response.text();
  check(
    "upstream status, contract headers and body preserved; unknown headers dropped",
    response.status === 429 &&
      text === "streamed-body" &&
      response.headers.get("retry-after") === "7" &&
      response.headers.get("x-ratelimit-limit") === "10" &&
      response.headers.get("x-platrixa-error") === "RATE_LIMITED" &&
      response.headers.get("x-request-id") === "rid-9" &&
      response.headers.get("x-internal-debug") === null &&
      response.headers.get("x-platrixa-management-token") === null &&
      response.headers.get("x-platrixa-assertion-signature") === null,
    `${response.status} ${text}`
  );
}

// ---------------------------------------------------------------------------
// Fixture for the Python backend-compatibility replay
// ---------------------------------------------------------------------------

function toFixtureEntry(call) {
  const url = new URL(call.url);
  return {
    method: call.method,
    path_with_query: url.pathname + url.search,
    headers: call.headers,
    body_b64: call.body.toString("base64"),
  };
}

// Signed-but-tampered entries: the fixture is signed for the stated request,
// and the Python replay deliberately changes one transport dimension.
const tamperRequests = [];
{
  reset();
  const post = await call("/v1/developer/api-keys", {
    method: "POST",
    body: '{"name":"tamper-target"}',
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const entry = toFixtureEntry(post.last);
  entry.name = "altered body";
  entry.send_body_b64 = Buffer.from('{"name":"different-body"}').toString("base64");
  tamperRequests.push(entry);
}
{
  reset();
  const query = await call("/v1/developer/requests?limit=5", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const entry = toFixtureEntry(query.last);
  entry.name = "altered query";
  entry.send_path_with_query = "/v1/developer/requests?limit=6";
  tamperRequests.push(entry);
}
{
  reset();
  const path = await call("/v1/developer/usage", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const entry = toFixtureEntry(path.last);
  entry.name = "altered path";
  entry.send_path_with_query = "/v1/developer/requests";
  tamperRequests.push(entry);
}
{
  reset();
  const ct = await call("/v1/developer/usage", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const entry = toFixtureEntry(ct.last);
  entry.name = "altered content-type";
  entry.send_headers = { "content-type": "text/plain" };
  tamperRequests.push(entry);
}
{
  reset();
  const method = await call("/v1/developer/api-keys", {
    method: "GET",
    headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
  });
  const entry = toFixtureEntry(method.last);
  entry.name = "altered method";
  entry.send_method = "POST";
  tamperRequests.push(entry);
}

// One entry replayed twice: the second send must hit replay protection.
reset();
const replayRequest = toFixtureEntry(
  (
    await call("/v1/developer/usage", {
      method: "GET",
      headers: { "x-platrixa-management-token": ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN },
    })
  ).last
);

const fixturePath = process.env.OPERATOR_ASSERTION_FIXTURE || "/tmp/operator_assertion_fixture.json";
fs.writeFileSync(
  fixturePath,
  JSON.stringify(
    {
      secret: SIGNING_SECRET,
      token: ENV.PLATRIXA_KEY_MANAGEMENT_TOKEN,
      tenant: ENV.PLATRIXA_KEY_MANAGEMENT_TENANT_ID,
      issuer: ENV.PLATRIXA_MANAGEMENT_ASSERTION_ISSUER,
      requests: FIXTURE,
      tamper_requests: tamperRequests,
      replay_request: replayRequest,
    },
    null,
    2
  )
);
console.log(`\nfixture written: ${fixturePath}`);

const failed = RESULTS.filter((r) => !r[1]);
console.log(`\n${RESULTS.length - failed.length}/${RESULTS.length} operator assertion checks passed`);
process.exit(failed.length === 0 ? 0 : 1);
