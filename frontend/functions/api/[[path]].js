// Platrixa — Cloudflare Pages API proxy (Phase 7I)
// ---------------------------------------------------------------------
// The production frontend is served from Cloudflare Pages (static), while
// FastAPI runs as a separate long-running service (see DEPLOYMENT.md).
// This Pages Function proxies every /api/* request to the FastAPI host
// configured in the Pages environment variable API_BACKEND_URL, so the
// frontend keeps its same-origin API base ("/api/v1/...").
//
// If API_BACKEND_URL is not set, requests fail fast with an explicit 502
// JSON body instead of Cloudflare's generic 405 — no backend URL is
// invented anywhere in this repository.
// ---------------------------------------------------------------------

const FORWARD_HEADERS = [
  "content-type",
  "accept",
  "accept-language",
  "x-requested-with",
  // Phase 5I (audit C2): same developer-contract identity headers as the
  // /v1 function — forwarded unchanged, never fabricated.
  // Browser-supplied x-platrixa-api-key is NOT forwarded; the server-side
  // website API key is injected instead (see below).
  // "x-platrixa-api-key",
  // Server-side injected; browser-supplied key is stripped.
  "idempotency-key",
  "x-request-id",
];

// CSRF protection: exact Origin header validation for browser state-changing requests.
// The configured public origin is provided via the PLATRIXA_PUBLIC_ORIGIN environment variable.
// If not set, Origin validation is skipped (fail-closed by rejecting state-changing
// requests when the origin cannot be verified).
// This check runs server-side in the Cloudflare edge function, so the origin
// configuration never exposes secrets to the browser.
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
  const backend = String(
    (context.env && context.env.API_BACKEND_URL) || ""
  ).replace(/\/+$/, "");

  if (!backend) {
    return new Response(
      JSON.stringify({
        error: "backend_not_configured",
        detail: "API_BACKEND_URL is not set on this Pages project.",
      }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      }
    );
  }

  const url = new URL(context.request.url);
  const target = backend + url.pathname + url.search;

  // --- CSRF Origin validation for state-changing requests ---
  // Validate Origin header against the exact configured public origin.
  // GET and HEAD are strictly read-only; all other methods require a trusted origin.
  if (requiresCSRFProtection(context.request.method)) {
    if (!validateCSRFOrigin(context.request, context.env)) {
      return new Response(
        JSON.stringify({
          error: "csrf_invalid_origin",
          detail: `Request Origin ${context.request.headers.get("origin") || "missing"} is not trusted.`,
        }),
        {
          status: 403,
          headers: { "content-type": "application/json" },
        }
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
    return new Response(
      JSON.stringify({
        error: "visitor_signing_not_configured",
        detail: "PLATRIXA_VISITOR_HMAC_SECRET is not set on this Pages project.",
      }),
      { status: 503, headers: { "content-type": "application/json" } }
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
  };
  if (bodyBytes.length > 0) {
    init.body = bodyBytes;
  }

  let upstream;
  try {
    upstream = await fetch(target, init);

    const respHeaders = new Headers(upstream.headers);
    respHeaders.set(
      "content-type",
      upstream.headers.get("content-type") || "application/json"
    );
    return new Response(upstream.body, {
      status: upstream.status,
      headers: respHeaders,
    });
  } catch (err) {
    return new Response(
      JSON.stringify({
        error: "proxy_failed",
        detail: err && err.message,
      }),
      {
        status: 502,
        headers: { "content-type": "application/json" },
      }
    );
  }
}