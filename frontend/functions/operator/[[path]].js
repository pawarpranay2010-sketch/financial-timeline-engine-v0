// Operator Ingress — Phase 1B (management gateway)
//
// Cloudflare Pages Function for the operator-only management gateway.
// It is NOT exposed to browser-controlled code: the hostname is an operator
// surface and the caller must present the management token, which this
// function compares (constant time, over SHA-256 digests) against the
// server-side binding before anything is forwarded.
//
// WHAT THIS FUNCTION DOES:
//   • Authorizes the CALLER with the static management token
//       (X-Platrixa-Management-Token, compared to PLATRIXA_KEY_MANAGEMENT_TOKEN)
//   • Derives the authoritative tenant from PLATRIXA_KEY_MANAGEMENT_TENANT_ID
//     — never from a request field
//   • Generates a FRESH management gateway assertion with Web Crypto over the
//     ACTUAL outgoing request (method, canonical path, raw query string,
//     exact content-type, SHA-256 of the exact body bytes, fresh nonce,
//     signed timestamp)
//   • Forwards the request with the 11 assertion headers and NO management
//     token: assertion-only authorization. The backend derives the tenant
//     from the verified assertion, never from a forwarded shared secret.
//
// WHAT THIS FUNCTION DOES NOT DO:
//   • Does NOT accept or forward a caller-supplied assertion, tenant, or
//     operator identity — any such header from the request is discarded
//   • Does NOT forward ANY management token upstream — neither the one the
//     caller presented nor the server-side binding. The static token never
//     leaves this edge function.
//   • Does NOT proxy to arbitrary paths (allowlist below) and never to a
//     different origin than API_BACKEND_URL
//
// REQUIREMENTS (configure externally, EDGE SIDE ONLY):
//   • PLATRIXA_MANAGEMENT_ASSERTION_SECRET   — HMAC-SHA256 signing secret
//   • PLATRIXA_MANAGEMENT_ASSERTION_ISSUER   — issuer id; MUST also appear in
//                                              the backend's
//                                              PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS
//   • PLATRIXA_KEY_MANAGEMENT_TOKEN          — static operator credential,
//                                              verified here, NEVER forwarded
//   • PLATRIXA_KEY_MANAGEMENT_TENANT_ID      — tenant the token is bound to
//   • API_BACKEND_URL                        — backend origin
//
// CANONICAL MESSAGE (must match backend/auth/api_keys.py exactly):
//   issuer|audience|tenant_id|method|path|query_string|content_type|body_hash|timestamp|nonce
//
// The path is the undecoded pathname of the URL actually fetched (trailing
// slash and percent-encoding preserved, so %2F never becomes a separator);
// the query is the raw query string without "?"; the content-type is the
// exact header value sent upstream; the body hash is SHA-256 of the exact
// bytes sent upstream (SHA-256 of empty bytes for an empty body).

const REQUEST_TIMEOUT_MS = 25_000;
const MANAGEMENT_AUDIENCE = "platrixa-management";
const ISSUER_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

// Every management endpoint this ingress may forward. Anything else is 404,
// so this never becomes a general-purpose proxy.
const MANAGEMENT_ROUTES = [
  { method: "POST", pattern: /^\/v1\/developer\/api-keys$/ },
  { method: "GET", pattern: /^\/v1\/developer\/api-keys$/ },
  { method: "POST", pattern: /^\/v1\/developer\/api-keys\/[^/]+\/revoke$/ },
  { method: "POST", pattern: /^\/v1\/developer\/api-keys\/[^/]+\/rotate$/ },
  { method: "GET", pattern: /^\/v1\/developer\/api-keys\/[^/]+\/usage$/ },
  { method: "GET", pattern: /^\/v1\/developer\/usage$/ },
  { method: "GET", pattern: /^\/v1\/developer\/requests$/ },
  { method: "GET", pattern: /^\/v1\/developer\/requests\/[^/]+$/ },
  // Self-service developer onboarding: owner dashboard contract. Same
  // assertion-only authorization as every other management route.
  { method: "GET", pattern: /^\/v1\/owner\/onboarding\/overview$/ },
  { method: "GET", pattern: /^\/v1\/owner\/onboarding\/applications$/ },
  { method: "GET", pattern: /^\/v1\/owner\/onboarding\/applications\/[^/]+$/ },
  { method: "POST", pattern: /^\/v1\/owner\/onboarding\/applications\/[^/]+\/(advance|provision|suspend|restore|reject)$/ },
  { method: "POST", pattern: /^\/v1\/owner\/onboarding\/tenants\/[^/]+\/quota$/ },
  { method: "POST", pattern: /^\/v1\/owner\/onboarding\/tenants\/[^/]+\/keys\/[^/]+\/revoke$/ },
  { method: "POST", pattern: /^\/v1\/owner\/onboarding\/tenants\/[^/]+\/keys\/rotate$/ },
  { method: "GET", pattern: /^\/v1\/owner\/onboarding\/notifications$/ },
  { method: "POST", pattern: /^\/v1\/owner\/onboarding\/notifications\/dispatch$/ },
  { method: "POST", pattern: /^\/v1\/owner\/onboarding\/maintenance\/expire$/ },
  // Legacy operator entry point, forwarded unchanged.
  { method: "POST", pattern: /^\/v1\/operator\/manage$/ },
];

// Contract headers relayed from the backend, plus its rate-limit headers.
const RESPONSE_HEADER_ALLOWLIST = new Set([
  "retry-after",
  "x-platrixa-error",
  "idempotent-replayed",
  "idempotency-key",
  "x-request-id",
]);

const encoder = new TextEncoder();

function errorEnvelope(status, code, message) {
  return new Response(JSON.stringify({ error: { code, message } }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function toHex(buffer) {
  const bytes = new Uint8Array(buffer);
  let out = "";
  for (let i = 0; i < bytes.length; i += 1) {
    out += bytes[i].toString(16).padStart(2, "0");
  }
  return out;
}

async function sha256Hex(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return toHex(digest);
}

async function hmacSha256Hex(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return toHex(signature);
}

/** Constant-time comparison of two strings via their SHA-256 digests. */
async function tokensMatch(a, b) {
  const [da, db] = await Promise.all([sha256Hex(encoder.encode(a)), sha256Hex(encoder.encode(b))]);
  if (da.length !== db.length) return false;
  let diff = 0;
  for (let i = 0; i < da.length; i += 1) {
    diff |= da.charCodeAt(i) ^ db.charCodeAt(i);
  }
  return diff === 0;
}

/**
 * The canonical management assertion message. Field order and the "|"
 * delimiter are the contract with backend/auth/api_keys.py; changing either
 * side without the other breaks every management call.
 */
function canonicalManagementMessage(fields) {
  return [
    fields.issuer,
    fields.audience,
    fields.tenant_id,
    fields.method,
    fields.path,
    fields.query_string,
    fields.content_type,
    fields.body_hash,
    fields.timestamp,
    fields.nonce,
  ].join("|");
}

/**
 * Builds a complete, freshly signed assertion for the outgoing request.
 * Returns the 11 header pairs the backend requires.
 */
async function buildManagementAssertion({
  secret,
  issuer,
  tenantId,
  method,
  path,
  queryString,
  contentType,
  bodyBytes,
}) {
  const bodyHash = await sha256Hex(bodyBytes);
  const timestamp = String(Math.floor(Date.now() / 1000));
  // 36 chars of [0-9a-f-]: satisfies the backend's
  // ^[A-Za-z0-9._:-]{16,128}$ nonce rule and is cryptographically random.
  const nonce = crypto.randomUUID();

  const fields = {
    issuer,
    audience: MANAGEMENT_AUDIENCE,
    tenant_id: tenantId,
    method,
    path,
    query_string: queryString,
    content_type: contentType,
    body_hash: bodyHash,
    timestamp,
    nonce,
  };
  const signature = await hmacSha256Hex(secret, canonicalManagementMessage(fields));

  return {
    "x-platrixa-assertion-signature": signature,
    "x-platrixa-assertion-issuer": issuer,
    "x-platrixa-assertion-audience": MANAGEMENT_AUDIENCE,
    "x-platrixa-assertion-tenant-id": tenantId,
    "x-platrixa-assertion-method": method,
    "x-platrixa-assertion-path": path,
    "x-platrixa-assertion-query-string": queryString,
    "x-platrixa-assertion-content-type": contentType,
    "x-platrixa-assertion-body-hash": bodyHash,
    "x-platrixa-assertion-timestamp": timestamp,
    "x-platrixa-assertion-nonce": nonce,
  };
}

function isManagementRoute(method, pathname) {
  return MANAGEMENT_ROUTES.some(
    (route) => route.method === method && route.pattern.test(pathname)
  );
}

/**
 * Normalizes the Pages mount prefix.
 *
 * This file lives at frontend/functions/operator/[[path]].js, and the Pages
 * build copies frontend/functions/ into the output as functions/ (see
 * DEPLOYMENT.md), so Pages routes it at /operator/*. The management contract
 * (and the backend) addresses /v1/*, so the mount prefix is stripped before
 * the allowlist match AND before signing, keeping the signed path identical to
 * the path actually requested upstream. A project whose root is this directory
 * receives bare /v1/* paths and is unaffected.
 */
function stripOperatorMount(pathname) {
  if (pathname === "/operator") return "/";
  if (pathname.startsWith("/operator/")) return pathname.slice("/operator".length);
  return pathname;
}

function relayResponseHeaders(upstream) {
  const headers = new Headers();
  for (const [name, value] of upstream.headers.entries()) {
    const lower = name.toLowerCase();
    if (RESPONSE_HEADER_ALLOWLIST.has(lower) || lower.startsWith("x-ratelimit-")) {
      headers.set(name, value);
    }
  }
  headers.set("content-type", upstream.headers.get("content-type") || "application/json");
  return headers;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();

    // Route allowlist first: no backend call, no credential use for anything
    // that is not a management path. `requestPath` has the Pages mount prefix
    // removed, so it is the path that will be signed and forwarded.
    const requestPath = stripOperatorMount(url.pathname);
    if (!isManagementRoute(method, requestPath)) {
      return errorEnvelope(404, "NOT_FOUND", "Operator management endpoint not found");
    }

    const secret = String(env?.PLATRIXA_MANAGEMENT_ASSERTION_SECRET || "").trim();
    const issuer = String(env?.PLATRIXA_MANAGEMENT_ASSERTION_ISSUER || "").trim();
    const tenantId = String(env?.PLATRIXA_KEY_MANAGEMENT_TENANT_ID || "").trim();
    const expectedToken = String(env?.PLATRIXA_KEY_MANAGEMENT_TOKEN || "").trim();
    const backend = String(env?.API_BACKEND_URL || "").trim();

    // Fail closed on incomplete configuration. The secret and issuer are
    // signing material: without them nothing can be signed.
    if (!secret) {
      return errorEnvelope(
        500,
        "MANAGEMENT_ASSERTION_SECRET_MISSING",
        "Management assertion secret not configured on this function"
      );
    }
    if (!issuer || !ISSUER_PATTERN.test(issuer)) {
      return errorEnvelope(
        500,
        "MANAGEMENT_ASSERTION_ISSUER_INVALID",
        "PLATRIXA_MANAGEMENT_ASSERTION_ISSUER is missing or malformed"
      );
    }
    // Caller authorization happens HERE, at the edge, and only here. The
    // operator credential is compared in constant time against the
    // server-side binding; the presented value is never forwarded upstream.
    const presentedToken = String(request.headers.get("x-platrixa-management-token") || "").trim();
    if (!presentedToken) {
      return errorEnvelope(
        401,
        "OPERATOR_UNAUTHENTICATED",
        "Operator identity must be established via authenticated management token"
      );
    }
    if (!expectedToken || !(await tokensMatch(presentedToken, expectedToken))) {
      return errorEnvelope(
        401,
        "OPERATOR_UNAUTHENTICATED",
        "Operator identity must be established via authenticated management token"
      );
    }
    // Tenant is configuration, never a request field.
    if (!tenantId) {
      return errorEnvelope(
        401,
        "OPERATOR_UNAUTHENTICATED",
        "Management token validated but tenant binding missing from configuration"
      );
    }
    if (!backend) {
      return errorEnvelope(
        500,
        "BACKEND_NOT_CONFIGURED",
        "API_BACKEND_URL not configured for operator ingress function"
      );
    }

    // Build the exact target URL that will be fetched; the assertion is
    // signed over THIS url's path and raw query so signature and transport
    // cannot diverge.
    let backendOrigin;
    try {
      backendOrigin = new URL(backend).origin;
    } catch (_err) {
      return errorEnvelope(500, "BACKEND_NOT_CONFIGURED", "API_BACKEND_URL is not a valid URL");
    }
    if (requestPath.startsWith("//")) {
      return errorEnvelope(404, "NOT_FOUND", "Operator management endpoint not found");
    }

    let targetUrl;
    try {
      targetUrl = new URL(backendOrigin + requestPath + url.search);
    } catch (_err) {
      return errorEnvelope(404, "NOT_FOUND", "Operator management endpoint not found");
    }
    if (targetUrl.origin !== backendOrigin) {
      return errorEnvelope(404, "NOT_FOUND", "Operator management endpoint not found");
    }

    const canonicalPath = targetUrl.pathname;
    const canonicalQuery = targetUrl.search.startsWith("?")
      ? targetUrl.search.slice(1)
      : targetUrl.search;

    // Body bytes: the ones actually forwarded, hashed as-is.
    const hasBody = method !== "GET" && method !== "HEAD";
    let bodyBytes = new Uint8Array(0);
    if (hasBody) {
      try {
        bodyBytes = new Uint8Array(await request.arrayBuffer());
      } catch (_err) {
        return errorEnvelope(500, "REQUEST_READ_ERROR", "Failed to read request body");
      }
    }

    const contentType = String(
      request.headers.get("content-type") || "application/json"
    );

    let assertionHeaders;
    try {
      assertionHeaders = await buildManagementAssertion({
        secret,
        issuer,
        tenantId,
        method,
        path: canonicalPath,
        queryString: canonicalQuery,
        contentType,
        bodyBytes,
      });
    } catch (_err) {
      return errorEnvelope(
        500,
        "MANAGEMENT_ASSERTION_SIGNING_FAILED",
        "Management assertion could not be generated"
      );
    }

    // Only contract headers are forwarded. Caller-supplied assertion,
    // tenant and operator headers are never copied.
    // The management token is never forwarded to the backend.
    const forwardHeaders = new Headers({
      "content-type": contentType,
      "x-platrixa-tenant-id": tenantId,
      "x-request-id": request.headers.get("x-request-id") || crypto.randomUUID(),
    });
    for (const [name, value] of Object.entries(assertionHeaders)) {
      forwardHeaders.set(name, value);
    }
    const apiKey = request.headers.get("x-platrixa-api-key");
    if (apiKey) {
      forwardHeaders.set("x-platrixa-api-key", apiKey);
    }
    const idempotencyKey = request.headers.get("idempotency-key");
    if (idempotencyKey) {
      forwardHeaders.set("idempotency-key", idempotencyKey);
    }

    const init = {
      method,
      headers: forwardHeaders,
      redirect: "follow",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    };
    if (hasBody) {
      init.body = bodyBytes;
    }

    let upstream;
    try {
      upstream = await fetch(targetUrl.toString(), init);
    } catch (err) {
      const timedOut = !!err && (err.name === "TimeoutError" || err.name === "AbortError");
      return timedOut
        ? errorEnvelope(504, "BACKEND_TIMEOUT", "The backend did not answer in time")
        : errorEnvelope(502, "BACKEND_UNREACHABLE", "The backend could not be reached");
    }

    // Status, body (streamed) and contract headers are preserved.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: relayResponseHeaders(upstream),
    });
  },
};
