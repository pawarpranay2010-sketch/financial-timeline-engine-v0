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
const VISITOR_COOKIE_NAME = "__Host-platrixa_visitor";
const VISITOR_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

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

// ---------------------------------------------------------------------
// SIGNED CLIENT-IP CLAIM (edge -> backend)
//
// Cloudflare sets cf-connecting-ip at its edge and a browser cannot choose
// it. This function is the ONLY hop that still sees the real visitor
// address (the backend's socket peer is the Cloudflare edge, and Railway
// rewrites x-forwarded-for / x-real-ip), so it asserts the address here.
//
// The claim is bound to THIS request by HMAC-ing over the visitor assertion
// signature, which the backend verifies and whose nonce it consumes exactly
// once. The backend trusts the claim only with a valid signature
// (backend/auth/client_ip_claim.py); a missing, malformed, forged or replayed
// claim falls back to the transport peer, which no header can forge.
//
// A browser-supplied x-platrixa-client-ip / -signature is never read: the
// forward loop below copies only FORWARD_HEADERS, and this function sets the
// two claim headers itself.
// ---------------------------------------------------------------------
const CLIENT_IP_CLAIM_HEADER = "x-platrixa-client-ip";
const CLIENT_IP_SIGNATURE_HEADER = "x-platrixa-client-ip-signature";
const CLIENT_IP_CANONICAL_PREFIX = "V1|platrixa-client-ip";

const IPV4_RE =
  /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])$/;
// Hex groups separated by colons; accepts "::" compression, rejects zone ids,
// ports, brackets and anything else a header could smuggle in.
const IPV6_RE = /^[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){2,7}$/;

/** The Cloudflare-written visitor address, or null when it is not an address. */
function trustedClientIp(request) {
  const raw = (request.headers.get("cf-connecting-ip") || "").trim();
  if (!raw || raw.length > 64) return null;
  if (!IPV4_RE.test(raw) && !IPV6_RE.test(raw)) return null;
  return raw;
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
  const incomingVisitorId = readVisitorCookie(context.request.headers.get("cookie"));
  // Validate incoming cookie against strict UUID format; reject malformed values.
  let visitorId;
  if (incomingVisitorId && VISITOR_ID_RE.test(incomingVisitorId)) {
    visitorId = incomingVisitorId;
  } else {
    // Generate a fresh UUID for a first-time or invalid-cookie visit.
    visitorId = crypto.randomUUID();
  }


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
  const assertionSignature = await hmacSha256Hex(visitorSecret, canonical);
  const assertion = canonical + "|" + assertionSignature;

  // --- Build forwarded headers ---
  // Start with server-injected API key
  if (serverApiKey) {
    headers.set("x-platrixa-api-key", serverApiKey);
  }
  // Edge-owned identity — overwrites any browser-supplied value.
  // The visitor cookie belongs on the RESPONSE to the browser (below), never
  // on the upstream request: a request header would hand the identity to the
  // backend and the browser would never store it. Issued only when the
  // incoming cookie is absent or invalid.
  const visitorCookie =
    incomingVisitorId == null || !VISITOR_ID_RE.test(incomingVisitorId)
      ? `${VISITOR_COOKIE_NAME}=${visitorId}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=2592000`
      : null;
  headers.set("x-platrixa-visitor-id", visitorId);
  headers.set("x-platrixa-visitor-assertion", assertion);
  headers.set("x-platrixa-request-timestamp", requestTimestamp);
  headers.set("x-platrixa-visitor-nonce", requestNonce);
  headers.set("x-platrixa-surface", "website");

  // Signed client-IP claim, bound to this request's visitor assertion.
  const clientIp = trustedClientIp(context.request);
  if (clientIp) {
    headers.set(
      CLIENT_IP_SIGNATURE_HEADER,
      await hmacSha256Hex(
        visitorSecret,
        `${CLIENT_IP_CANONICAL_PREFIX}|${clientIp}|${assertionSignature}`
      )
    );
    headers.set(CLIENT_IP_CLAIM_HEADER, clientIp);
  }
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
    // Preserve every Set-Cookie the backend sent (never overwrite one), then
    // add the edge-owned visitor cookie. `new Headers()` can collapse repeated
    // Set-Cookie entries, so re-add them individually from getSetCookie().
    if (typeof upstream.headers.getSetCookie === "function") {
      const upstreamCookies = upstream.headers.getSetCookie();
      if (upstreamCookies.length > 0) {
        respHeaders.delete("set-cookie");
        for (const cookie of upstreamCookies) {
          respHeaders.append("set-cookie", cookie);
        }
      }
    }
    if (visitorCookie) respHeaders.append("set-cookie", visitorCookie);
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