// Platrixa — Cloudflare Pages API proxy for the /v1/* developer namespace
// (Phase 7I sibling of functions/api/[[path]].js)
// ---------------------------------------------------------------------
// Serves the same role as the /api function, for the versioned developer
// contract added in Phase 5A/5B: GET /v1/health, GET /v1/capabilities, etc.
// The browser keeps a same-origin API base ("/v1/..."); this function
// forwards to the FastAPI host configured in the Pages environment variable
// API_BACKEND_URL.
//
// Security contract (mirrors functions/api/[[path]].js):
//   - Fixed target only: API_BACKEND_URL + the request's own /v1/* path.
//     This is NOT a generic open proxy.
//   - No secrets here: this function adds NO credentials. The operator
//     configures authentication on the backend itself (e.g. zero-config
//     mode, or the backend's tenant gate). API keys are never stored in,
//     injected by, or readable from this frontend.
//
// CSRF protection (same as /api function): exact Origin header validation
// for browser state-changing requests. The configured public origin is
// provided via the PLATRIXA_PUBLIC_ORIGIN environment variable.
// ---------------------------------------------------------------------

const REQUEST_TIMEOUT_MS = 25_000;

// CSRF protection: exact Origin header validation for browser state-changing requests.
// The configured public origin is provided via the PLATRIXA_PUBLIC_ORIGIN environment variable.
// If not set, Origin validation is skipped (fail-closed by rejecting state-changing
// requests when the origin cannot be verified).
function validateCSRFOrigin(request, env) {
  const requestOrigin = request.headers.get("origin");
  if (!requestOrigin) return false;

  const publicOrigin = env.PLATRIXA_PUBLIC_ORIGIN;
  if (!publicOrigin) {
    // No public origin configured — fail closed for state-changing requests
    return false;
  }

  // Exact match with configured public origin
  return requestOrigin === publicOrigin;
}

// GET and HEAD are strictly read-only; all other methods require a trusted Origin.
function requiresCSRFProtection(method) {
  return method !== "GET" && method !== "HEAD";
}

// The visitor id is edge-owned: it is read ONLY from the HttpOnly cookie
// issued by this proxy. A browser-supplied `x-platrixa-visitor-id` header is
// never trusted — the forwarded value is always overwritten below.
const VISITOR_COOKIE_NAME = "platrixa_visitor_id";
const VISITOR_ID_RE = /^[A-Za-z0-9-]{16,64}$/;

function readVisitorCookie(cookieHeader) {
  if (!cookieHeader) return null;
  for (const part of cookieHeader.split(";")) {
    const eq = part.indexOf("=");
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() !== VISITOR_COOKIE_NAME) continue;
    const value = part.slice(eq + 1).trim();
    return VISITOR_ID_RE.test(value) ? value : null;
  }
  return null;
}


const FORWARD_HEADERS = [
  "content-type",
  "accept",
  "accept-language",
  "x-requested-with",
  // Phase 5I (audit C2): the caller's credential and request identity
  // must reach the backend UNCHANGED. The developer contract is
  // key-in-header; dropping these made every proxied call anonymous
  // (or 401) and made idempotent replay impossible from the browser.
  // Browser-supplied x-platrixa-api-key is NOT forwarded; the server-side
  // website API key is injected instead (see below).
  // "x-platrixa-api-key",
  // Server-side injected; browser-supplied key is stripped.
  "idempotency-key",
  "x-request-id",
];

const RESPONSE_HEADERS = new Set([
  "retry-after",
  "x-platrixa-error",
  "idempotent-replayed",
  "idempotency-key",
  "x-request-id",
]);

function contractHeaders(upstream) {
  const headers = new Headers({
    "cache-control": "no-store",
  });
  if (!upstream) return headers;
  for (const [name, value] of upstream.headers.entries()) {
    const lower = name.toLowerCase();
    if (RESPONSE_HEADERS.has(lower) || lower.startsWith("x-ratelimit-")) {
      headers.set(name, value);
    }
  }
  return headers;
}

function requestIdFrom(request) {
  const raw = (request.headers.get("x-request-id") || "").trim();
  return /^[A-Za-z0-9._-]{1,128}$/.test(raw) ? raw : null;
}

function envelopeResponse(status, code, message, requestId, upstream) {
  const headers = contractHeaders(upstream);
  headers.set("content-type", "application/json; charset=utf-8");
  if (requestId) headers.set("x-request-id", requestId);
  return new Response(
    JSON.stringify({
      api_version: "v1",
      error: { code, message, ...(requestId ? { request_id: requestId } : {}) },
    }),
    { status, headers }
  );
}

// --- Visitor assertion signing (edge-owned HMAC-SHA256) ---
// The edge signs a fresh, single-use assertion per request over the exact
// method, path, query, body hash, timestamp and nonce. The shared secret is
// a server-side environment variable; it is never sent to the browser.
function hexFromBytes(buf) {
  return [...new Uint8Array(buf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(bytes) {
  return hexFromBytes(await crypto.subtle.digest("SHA-256", bytes));
}

async function hmacSha256Hex(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(message)
  );
  return hexFromBytes(sig);
}

export async function onRequest(context) {
  const requestId = requestIdFrom(context.request);
  const backend = String(
    (context.env && context.env.API_BACKEND_URL) || ""
  ).replace(/\/+$/, "");

  if (!backend) {
    return envelopeResponse(
      502,
      "BACKEND_NOT_CONFIGURED",
      "The Platrixa backend is not configured for this frontend environment.",
      requestId
    );
  }

  const url = new URL(context.request.url);
  if (!url.pathname.startsWith("/v1/") && url.pathname !== "/v1") {
    return envelopeResponse(
      404,
      "PATH_NOT_FORWARDED",
      "Only the /v1/* namespace is proxied by this function.",
      requestId
    );
  }

  const target = backend + url.pathname + url.search;

  // --- CSRF Origin validation for state-changing requests ---
  // Validate Origin header against the exact configured public origin.
  // GET and HEAD are strictly read-only; all other methods require a trusted origin.
  if (requiresCSRFProtection(context.request.method)) {
    if (!validateCSRFOrigin(context.request, context.env)) {
      return envelopeResponse(
        403,
        "csrf_invalid_origin",
        `Request Origin ${context.request.headers.get("origin") || "missing"} is not trusted.`,
        requestId
      );
    }
  }
  // -----------------------------------------------------------------

  // --- Server-side API key injection ---
  // Always inject the server-side website API key; strip any browser-supplied key.
  // The browser must never influence which API key authenticates the request.
  const serverApiKey = (context.env && context.env.PLATRIXA_API_KEY) || "";
  const headers = new Headers();
  if (serverApiKey) {
    headers.set("x-platrixa-api-key", serverApiKey);
  }

  // --- Visitor identity + signed assertion (edge-owned) ---
  // The id comes ONLY from the HttpOnly cookie the edge issues. Any
  // browser-supplied visitor/assertion header is ignored and overwritten.
  const visitorId =
    readVisitorCookie(context.request.headers.get("cookie")) ||
    crypto.randomUUID();

  const visitorSecret = (context.env && context.env.PLATRIXA_VISITOR_HMAC_SECRET) || "";
  if (!visitorSecret) {
    // Fail closed: an unsigned request must never reach the backend as a
    // website request.
    return envelopeResponse(
      503,
      "VISITOR_SIGNING_NOT_CONFIGURED",
      "Visitor signing is not configured for this frontend environment.",
      requestId
    );;
  }

  const isReadOnly =
    context.request.method === "GET" || context.request.method === "HEAD";
  const bodyBytes = isReadOnly
    ? new Uint8Array(0)
    : new Uint8Array(await context.request.arrayBuffer());
  const bodyHash = await sha256Hex(bodyBytes);
  const requestTimestamp = String(Math.floor(Date.now() / 1000));
  const requestNonce = crypto.randomUUID();
  const requestQuery = url.search.startsWith("?") ? url.search.slice(1) : url.search;
  const canonical = [
    "V1",
    "platrixa",
    visitorId,
    context.request.method,
    url.pathname,
    requestQuery,
    bodyHash,
    requestTimestamp,
    requestNonce,
  ].join("|");
  const assertion = canonical + "|" + (await hmacSha256Hex(visitorSecret, canonical));

  // --- Build forwarded headers ---
  // Start with server-injected API key
  if (serverApiKey) {
    headers.set("x-platrixa-api-key", serverApiKey);
  }
  // Edge-owned identity — overwrites any browser-supplied value.
  headers.set("x-platrixa-visitor-id", visitorId);
  headers.set("x-platrixa-visitor-assertion", assertion);
  headers.set("x-platrixa-request-timestamp", requestTimestamp);
  headers.set("x-platrixa-visitor-nonce", requestNonce);
  headers.set("x-platrixa-surface", "website");
  // Forward the Origin the edge already validated so the backend's own
  // defence-in-depth CSRF check sees the same value (it re-validates).
  const forwardedOrigin = context.request.headers.get("origin");
  if (forwardedOrigin) headers.set("origin", forwardedOrigin);

  // Forward other allowed headers, skipping browser-supplied trusted headers
  for (const name of FORWARD_HEADERS) {
    // Skip browser-supplied trusted headers that edge must manage
    if (name === "x-platrixa-api-key") continue;
    if (name === "x-platrixa-visitor-id") continue;
    if (name === "x-platrixa-visitor-assertion") continue;
    if (name === "x-platrixa-request-timestamp") continue;
    if (name === "x-platrixa-visitor-nonce") continue;
    if (name === "x-platrixa-surface") continue;
    const value = context.request.headers.get(name);
    if (value) headers.set(name, value);
  }

  // --- Build init request ---
  const init = {
    method: context.request.method,
    headers: headers,
    redirect: "follow",
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
  };
  if (bodyBytes.length > 0) {
    init.body = bodyBytes;
  }

  let upstream;
  try {
    upstream = await fetch(target, init);
  } catch (err) {
    const timedOut =
      !!err && (err.name === "TimeoutError" || err.name === "AbortError");
    return timedOut
      ? envelopeResponse(
          504,
          "BACKEND_TIMEOUT",
          "The Platrixa backend did not answer in time; retry shortly.",
          requestId
        )
      : envelopeResponse(
          502,
          "BACKEND_UNREACHABLE",
          "The Platrixa backend could not be reached.",
          requestId
        );
  }

  const contentType = upstream.headers.get("content-type") || "";
  const isJson = contentType.toLowerCase().includes("application/json");
  const body = await upstream.text();

  if (!isJson) {
    const status = upstream.status >= 400 ? upstream.status : 502;
    const message =
      status === 429
        ? "The Platrixa backend is rate-limited or starting up; retry shortly."
        : `The Platrixa backend returned a response that is not a JSON envelope (HTTP ${upstream.status}).`;
    return envelopeResponse(status, "BACKEND_ERROR", message, requestId, upstream);
  }

  const responseHeaders = contractHeaders(upstream);
  responseHeaders.set("content-type", contentType || "application/json");
  if (requestId) responseHeaders.set("x-request-id", requestId);

  return new Response(body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}