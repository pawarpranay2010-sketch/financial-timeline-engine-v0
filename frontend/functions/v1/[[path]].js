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


// ---------------------------------------------------------------------
// PUBLIC WEBSITE ROUTE POLICY — explicit allowlist, default deny.
//
// This proxy authenticates the browser as the SHARED website tenant (it
// injects PLATRIXA_API_KEY). Forwarding the whole /v1 namespace therefore
// made tenant-scoped management routes reachable with that shared
// credential. Only the routes the website actually uses are forwarded,
// matched by METHOD and EXACT canonical path.
//
// Canonical table: frontend/web/lib/website-route-policy.js (the Next
// dev/preview proxy imports it). Pages Functions cannot import outside the
// functions directory, so this copy is kept byte-equivalent in meaning and
// tests/test_onboarding_remediation.py asserts both tables are identical.
// ---------------------------------------------------------------------
const WEBSITE_ROUTE_SPECS = [
  // Liveness / read-only contract
  { methods: ["GET", "HEAD"], pattern: "^/v1/health$" },
  { methods: ["GET", "HEAD"], pattern: "^/v1/ready$" },
  { methods: ["GET", "HEAD"], pattern: "^/v1/capabilities$" },
  { methods: ["GET", "HEAD"], pattern: "^/v1/usage$" },

  // Deterministic processing contract used by the site/demo
  { methods: ["POST"], pattern: "^/v1/process$" },
  { methods: ["POST"], pattern: "^/v1/process/document$" },
  { methods: ["POST"], pattern: "^/v1/documents$" },
  { methods: ["POST"], pattern: "^/v1/inference-jobs$" },
  { methods: ["GET", "HEAD"], pattern: "^/v1/jobs/[A-Za-z0-9._:-]{1,128}$" },
  { methods: ["GET", "HEAD"], pattern: "^/v1/results/[A-Za-z0-9._:-]{1,128}$" },
  { methods: ["POST"], pattern: "^/v1/webhook-endpoints$" },
  { methods: ["GET", "HEAD"], pattern: "^/v1/webhook-endpoints$" },

  // Self-service developer onboarding — PUBLIC endpoints only.
  { methods: ["POST"], pattern: "^/v1/developer/access-requests$" },
  { methods: ["POST"], pattern: "^/v1/developer/verify-email$" },
  { methods: ["POST"], pattern: "^/v1/developer/resend-verification$" },
  { methods: ["GET", "HEAD"], pattern: "^/v1/developer/access-status$" },
  // Token-gated one-time key retrieval (the link in the onboarding email).
  { methods: ["POST"], pattern: "^/v1/developer/keys/reveal$" },

  // DELIBERATELY ABSENT — never forwarded from the public website:
  //   POST /v1/developer/keys/rotate   (shared-tenant credential rotation)
  //   GET  /v1/developer/jobs          (tenant-scoped job history)
  //   /v1/developer/api-keys/*, /v1/developer/usage, /v1/developer/requests
  //   /v1/owner/*, /v1/operator/*, /v1/internal/*
];

const WEBSITE_ROUTES = WEBSITE_ROUTE_SPECS.map((spec) => ({
  methods: spec.methods,
  pattern: new RegExp(spec.pattern),
}));

const MAX_WEBSITE_PATH_LENGTH = 256;

/** Canonical pathname, or null when the request is not already canonical. */
function normalizeWebsitePath(rawPathname) {
  if (typeof rawPathname !== "string" || rawPathname.length === 0) return null;
  if (rawPathname.length > MAX_WEBSITE_PATH_LENGTH) return null;
  if (rawPathname[0] !== "/") return null;
  // Percent-encoding can smuggle separators/dots past prefix checks.
  if (rawPathname.includes("%")) return null;
  if (rawPathname.includes("\\")) return null;
  if (rawPathname.includes("//")) return null;
  // No dot segments (also rejects "/." and "/..").
  if (/(^|\/)\.{1,2}(\/|$)/.test(rawPathname)) return null;
  // No trailing slash: "/v1/process/" must not match "/v1/process".
  if (rawPathname.length > 1 && rawPathname.endsWith("/")) return null;
  // Absolute-ASCII only: no spaces, control characters, semicolons, unicode.
  if (!/^[A-Za-z0-9\-._~\/]+$/.test(rawPathname)) return null;
  return rawPathname;
}

/** Default-deny: true only for an allowlisted method + exact canonical path. */
function isAllowedWebsiteRoute(method, pathname) {
  const normalized = normalizeWebsitePath(pathname);
  if (normalized === null) return false;
  const upper = String(method || "").toUpperCase();
  return WEBSITE_ROUTES.some(
    (route) => route.methods.includes(upper) && route.pattern.test(normalized)
  );
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

/**
 * Preserve every Set-Cookie the backend sent, then add the edge-owned visitor
 * cookie. Upstream cookies are never overwritten or collapsed.
 */
function appendVisitorCookies(headers, upstream, visitorCookie) {
  if (!upstream || !headers) return;
  const upstreamCookies =
    typeof upstream.headers.getSetCookie === "function"
      ? upstream.headers.getSetCookie()
      : [];
  for (const cookie of upstreamCookies) headers.append("set-cookie", cookie);
  if (visitorCookie) headers.append("set-cookie", visitorCookie);
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
  // DEFAULT DENY. The path must be canonical AND explicitly allowlisted for
  // this method; everything else never reaches the backend and never sees the
  // shared website credential.
  if (!isAllowedWebsiteRoute(context.request.method, url.pathname)) {
    return envelopeResponse(
      404,
      "PATH_NOT_FORWARDED",
      "This endpoint is not available through the public website API.",
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
  const assertionSignature = await hmacSha256Hex(visitorSecret, canonical);
  const assertion = canonical + "|" + assertionSignature;

  // --- Build forwarded headers ---
  // The ONLY credential that reaches the backend is the server-side website
  // key set above. Browser-supplied credentials are never copied: the header
  // loop below only forwards an explicit allowlist, and `authorization`,
  // `cookie`, `x-platrixa-management-token` and every `x-platrixa-*` authority
  // header are absent from it. There is no path by which the browser can
  // choose or override the credential.
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
    const enclosed = envelopeResponse(status, "BACKEND_ERROR", message, requestId, upstream);
    appendVisitorCookies(enclosed.headers, upstream, visitorCookie);
    return enclosed;
  }

  const responseHeaders = contractHeaders(upstream);
  responseHeaders.set("content-type", contentType || "application/json");
  if (requestId) responseHeaders.set("x-request-id", requestId);
  appendVisitorCookies(responseHeaders, upstream, visitorCookie);

  return new Response(body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}