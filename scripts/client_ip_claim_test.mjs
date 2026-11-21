#!/usr/bin/env node
/**
 * Client-IP claim tests for the Cloudflare Pages proxies (Phase 2).
 *
 * The backend trusts a client address only when the edge signs it and binds it
 * to the same request's visitor assertion (backend/auth/client_ip_claim.py).
 * These tests exercise the REAL edge functions — not a reimplementation — by
 * importing each one from its source and inspecting the headers it forwards.
 *
 * Asserted here:
 *   * the canonical string and the golden vector match the Python verifier
 *     (tests/fixtures/client_ip_claim_vector.json);
 *   * two visitors produce two different claims;
 *   * a browser-supplied claim header is ignored and overwritten;
 *   * a missing or malformed cf-connecting-ip produces NO claim (so the
 *     backend falls back to a non-forgeable identity instead of a guess).
 *
 * Run:
 *   node scripts/client_ip_claim_test.mjs
 */
import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const vector = JSON.parse(
  readFileSync(path.join(root, "tests", "fixtures", "client_ip_claim_vector.json"), "utf8")
);

const CLAIM = "x-platrixa-client-ip";
const CLAIM_SIGNATURE = "x-platrixa-client-ip-signature";
const ASSERTION = "x-platrixa-visitor-assertion";

const ENV = {
  API_BACKEND_URL: "https://backend.platrixa.test",
  PLATRIXA_VISITOR_HMAC_SECRET: vector.secret,
  PLATRIXA_API_KEY: "test-website-key",
  PLATRIXA_PUBLIC_ORIGIN: "https://platrixa.test",
};

/** Import an ES module from a file whose package.json type is unknown. */
async function loadModule(file) {
  const source = readFileSync(file, "utf8");
  const url = "data:text/javascript;base64," + Buffer.from(source, "utf8").toString("base64");
  return import(url);
}

/** The documented canonical string, computed independently of the functions. */
function expectedSignature(clientIp, assertionSignature) {
  return createHmac("sha256", vector.secret)
    .update(`${vector.canonical_prefix}|${clientIp}|${assertionSignature}`)
    .digest("hex");
}

let forwarded = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (url, init) => {
  forwarded.push({ url: String(url), headers: new Headers(init?.headers) });
  return new Response(JSON.stringify({ api_version: "v1", ok: true }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
};

async function forwardedHeaders(fn, { clientIp, extraHeaders = {}, pathName, method = "GET" }) {
  forwarded = [];
  const headers = new Headers();
  if (clientIp) headers.set("cf-connecting-ip", clientIp);
  for (const [name, value] of Object.entries(extraHeaders)) headers.set(name, value);
  const request = new Request("https://platrixa.pages.dev" + pathName, { method, headers });
  const response = await fn({ request, env: ENV });
  assert.equal(response.status, 200, `expected the proxy to reach the backend, got ${response.status}`);
  assert.equal(forwarded.length, 1, "exactly one upstream request");
  return forwarded[0].headers;
}

function assertionSignatureOf(headers) {
  const assertion = headers.get(ASSERTION) || "";
  const parts = assertion.split("|");
  assert.equal(parts.length, 10, "a visitor assertion must have 10 fields");
  return parts[9];
}

const tests = [];
function test(name, run) {
  tests.push({ name, run });
}

test("the golden vector agrees with the JavaScript signer", () => {
  for (const entry of vector.vectors) {
    assert.equal(
      expectedSignature(entry.client_ip, vector.assertion_signature),
      entry.signature,
      `vector for ${entry.client_ip}`
    );
  }
});

const FUNCTIONS = [
  { label: "/v1 proxy", file: path.join(root, "frontend", "functions", "v1", "[[path]].js"), pathName: "/v1/developer/access-status" },
  { label: "/api proxy", file: path.join(root, "frontend", "functions", "api", "[[path]].js"), pathName: "/api/v1/health" },
];

for (const target of FUNCTIONS) {
  test(`${target.label}: signs the client address and binds it to the assertion`, async () => {
    const mod = await loadModule(target.file);
    const headers = await forwardedHeaders(mod.onRequest, {
      clientIp: "203.0.113.7",
      pathName: target.pathName,
    });
    assert.equal(headers.get("x-platrixa-surface"), "website");
    assert.equal(headers.get(CLAIM), "203.0.113.7");
    assert.equal(
      headers.get(CLAIM_SIGNATURE),
      expectedSignature("203.0.113.7", assertionSignatureOf(headers)),
      "claim signature must cover the exact address and this request's assertion"
    );
  });

  test(`${target.label}: two visitors get two different claims`, async () => {
    const mod = await loadModule(target.file);
    const first = await forwardedHeaders(mod.onRequest, {
      clientIp: "203.0.113.7",
      pathName: target.pathName,
    });
    const second = await forwardedHeaders(mod.onRequest, {
      clientIp: "198.51.100.4",
      pathName: target.pathName,
    });
    assert.equal(first.get(CLAIM), "203.0.113.7");
    assert.equal(second.get(CLAIM), "198.51.100.4");
    assert.notEqual(first.get(CLAIM_SIGNATURE), second.get(CLAIM_SIGNATURE));
  });

  test(`${target.label}: a browser-supplied claim is ignored and overwritten`, async () => {
    const mod = await loadModule(target.file);
    const headers = await forwardedHeaders(mod.onRequest, {
      clientIp: "203.0.113.7",
      pathName: target.pathName,
      extraHeaders: {
        [CLAIM]: "1.2.3.4",
        [CLAIM_SIGNATURE]: "f".repeat(64),
      },
    });
    assert.equal(headers.get(CLAIM), "203.0.113.7", "the spoofed address must not survive");
    assert.notEqual(headers.get(CLAIM_SIGNATURE), "f".repeat(64));
    assert.equal(
      Number(headers.get(CLAIM_SIGNATURE).length),
      64,
      "a freshly computed signature is forwarded"
    );
  });

  test(`${target.label}: no cf-connecting-ip means no claim at all`, async () => {
    const mod = await loadModule(target.file);
    const headers = await forwardedHeaders(mod.onRequest, { pathName: target.pathName });
    assert.equal(headers.get(CLAIM), null);
    assert.equal(headers.get(CLAIM_SIGNATURE), null);
    // The request is still a valid website request; the backend falls back to
    // its non-forgeable identity rather than inventing one.
    assert.equal(headers.get("x-platrixa-surface"), "website");
  });

  for (const bad of ["not-an-ip", "203.0.113.7, 198.51.100.4", "203.0.113.7:443", "[2001:db8::1]", "fe80::1%eth0"]) {
    test(`${target.label}: malformed cf-connecting-ip ${JSON.stringify(bad)} is dropped`, async () => {
      const mod = await loadModule(target.file);
      const headers = await forwardedHeaders(mod.onRequest, {
        clientIp: bad,
        pathName: target.pathName,
      });
      assert.equal(headers.get(CLAIM), null);
      assert.equal(headers.get(CLAIM_SIGNATURE), null);
    });
  }
}

try {
  let failures = 0;
  for (const { name, run } of tests) {
    try {
      await run();
      console.log(`ok   ${name}`);
    } catch (error) {
      failures += 1;
      console.error(`FAIL ${name}\n     ${error && error.message ? error.message : error}`);
    }
  }
  console.log(`\n${tests.length - failures}/${tests.length} passed`);
  globalThis.fetch = realFetch;
  if (failures > 0) process.exit(1);
} finally {
  globalThis.fetch = realFetch;
}
