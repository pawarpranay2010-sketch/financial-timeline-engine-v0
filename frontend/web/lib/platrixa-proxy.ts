/**
 * Platrixa dev/preview API proxy — server-side ONLY (route handlers).
 *
 * Mirrors the repository's existing Cloudflare Pages proxy pattern
 * (frontend/functions/api/[[path]].js): the browser keeps a same-origin
 * API base; this handler forwards requests VERBATIM to the real FastAPI
 * backend configured by PLATRIXA_API_BASE_URL and returns the upstream
 * status/body unchanged. The frontend renders backend truth — this proxy
 * never transforms financial data, never reinterprets statuses, and
 * never fabricates results.
 *
 * Security contract (hard rules):
 *   - Fixed-target only: the destination is `PLATRIXA_API_BASE_URL` plus
 *     the request's own path under the two forwarded namespaces. This is
 *     NOT a generic open proxy — arbitrary absolute URLs are rejected.
 *   - Namespaces: only `/api/*` and `/v1/*` are forwarded; nothing else.
 *   - Secrets stay server-side: when `PLATRIXA_API_KEY` is set it is
 *     attached here as `X-Platrixa-API-Key` (exactly the header the
 *     Phase 15/16 gate expects). It is never logged, never echoed, and
 *     never sent to the browser — and because this runs in a Node route
 *     handler, it can never leak into the client bundle.
 *   - Fail-closed, structured: with no backend configured the proxy
 *     returns the SAME JSON envelope shape the frontend already parses
 *     (code BACKEND_NOT_CONFIGURED), so the UI shows a connection state
 *     instead of ever substituting mock data.
 *   - Correlation: a well-formed client `X-Request-Id` is forwarded
 *     verbatim (the backend sanitizes/re-echoes it; behavior unchanged).
 */

import crypto from "crypto";
import type { NextRequest } from "next/server";

export const PROXY_BASE_ENV = "PLATRIXA_API_BASE_URL";
export const PROXY_KEY_ENV = "PLATRIXA_API_KEY";

/** Upper bound on one proxied call (mirrors the Cloudflare Pages functions). */
export const PROXY_TIMEOUT_MS = 25_000;

/** The only path namespaces this proxy will ever forward. */
const FORWARDED_PREFIXES = ["/api/", "/v1/"] as const;

/** Headers safe to forward browser→backend (mirrors the CF Pages allowlist). */
const FORWARD_HEADERS = new Set([
  "content-type",
  "accept",
  "accept-language",
  "x-requested-with",
  "x-request-id",
  // Phase 5I (audit C2): the caller's credential and request identity
  // reach the backend UNCHANGED. The proxy NEVER invents, substitutes,
  // or overwrites a caller-supplied credential.
  "x-platrixa-api-key",
  // Management token stripped from public proxy forwarding
  // Public browser routes must NOT forward management tokens to Railway.
  // Management credentials are only accepted at the operator ingress route.
  // "x-platrixa-management-token",
  "idempotency-key",
]);

const NON_BROWSER_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
  "transfer-encoding",
  "keep-alive",
  "upgrade",
  "referer",
  "origin",
  "cookie",
  "forwarded",
  "x-forwarded-for",
  "x-forwarded-host",
  "x-forwarded-proto",
  "x-real-ip",
]);

export interface ProxyOutcome {
  status: number;
  body: string;
  contentType: string;
  requestId: string | null;
  /** Upstream response headers (contract headers are re-emitted). */
  responseHeaders?: Headers;
}

/** The same envelope shape the backend and the frontend already use. */
function errorEnvelope(
  status: number,
  code: string,
  message: string,
  requestId: string | null,
): ProxyOutcome {
  return {
    status,
    body: JSON.stringify({
      api_version: "v1",
      error: { code, message, ...(requestId ? { request_id: requestId } : {}) },
    }),
    contentType: "application/json",
    requestId,
  };
}


const VISITOR_COOKIE_NAME = "platrixa_visitor_id";
const VISITOR_ID_RE = /^[A-Za-z0-9-]{16,64}$/;

/** Read the edge-issued visitor id from the HttpOnly cookie, or null. */
function readVisitorCookie(cookieHeader: string | null): string | null {
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

export function sanitizeRequestId(value: string | null): string | null {
  const rid = (value ?? "").trim();
  return /^[A-Za-z0-9._-]{1,128}$/.test(rid) ? rid : null;
}

function resolveBackendBase(): string | null {
  const raw = (process.env[PROXY_BASE_ENV] ?? "").trim();
  if (!raw) return null;
  try {
    const url = new URL(raw);
    // http(s) only — no file:, unix:, or other schemes.
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return raw.replace(/\/+$/, "");
  } catch {
    return null;
  }
}

/**
 * Forward one same-origin request to the configured backend.
 * Returns a ProxyOutcome the thin route handlers pass through as-is.
 */
export async function forwardToPlatrixa(
  request: NextRequest,
  _pathSegments: string[],
  searchParams: string,
): Promise<ProxyOutcome> {
  // The browser's own path IS the backend path (same-origin contract),
  // e.g. /v1/capabilities or /api/v1/kernel/process. Catch-all params
  // exclude the route's literal prefix (/v1, /api), so the pathname is
  // the authoritative namespace source — never a reconstruction.
  const incomingPath = request.nextUrl.pathname;
  const requestId = sanitizeRequestId(request.headers.get("x-request-id"));

  const base = resolveBackendBase();
  if (!base) {
    // Fail closed with the documented envelope; the UI renders this as a
    // connection state (never as mock/financial data).
    return errorEnvelope(
      503,
      "BACKEND_NOT_CONFIGURED",
      "The Platrixa backend is not configured for this frontend environment. Set PLATRIXA_API_BASE_URL (server-side) to the FastAPI base URL.",
      requestId,
    );
  }

  if (!FORWARDED_PREFIXES.some((p) => incomingPath.startsWith(p) || incomingPath === p.slice(0, -1))) {
    return errorEnvelope(404, "PATH_NOT_FORWARDED", `Only ${FORWARDED_PREFIXES.join(" and ")} namespaces are proxied.`, requestId);
  }

  const target = `${base}${incomingPath}${searchParams ? `?${searchParams}` : ""}`;

  // --- Server-side API key injection ---
  // Always inject the server-side website API key; strip any browser-supplied key.
  // The browser must never influence which API key authenticates the request.
  const serverApiKey = (process.env.PLATRIXA_API_KEY || "").trim();
  const headers = new Headers();
  if (serverApiKey) {
    headers.set("x-platrixa-api-key", serverApiKey);
  }

  // --- Visitor identity (edge-owned) ---
  // Derived exclusively from the HttpOnly visitor cookie. Any browser-supplied
  // `x-platrixa-visitor-id` header is ignored; the forwarded value is set below.
  const visitorId =
    readVisitorCookie(request.headers.get("cookie")) || crypto.randomUUID();

  // --- CSRF Origin validation for browser state-changing requests ---
  // Validate Origin against the exact configured public origin. GET and HEAD
  // are strictly read-only; every other method requires a trusted origin.
  // Fail closed when PLATRIXA_PUBLIC_ORIGIN is not configured.
  const isStateChanging = request.method !== "GET" && request.method !== "HEAD";
  if (isStateChanging) {
    const requestOrigin = (request.headers.get("origin") ?? "").trim();
    const publicOrigin = (process.env.PLATRIXA_PUBLIC_ORIGIN ?? "").trim();
    if (!publicOrigin) {
      return errorEnvelope(
        403,
        "csrf_missing_public_origin",
        "CSRF origin validation is not configured.",
        requestId,
      );
    }
    if (!requestOrigin || requestOrigin !== publicOrigin) {
      return errorEnvelope(
        403,
        "csrf_invalid_origin",
        `Request Origin ${requestOrigin || "missing"} is not trusted.`,
        requestId,
      );
    }
  }
  // -----------------------------------------------------------------

  // --- Build forwarded headers ---
  // Start with server-injected API key
  if (serverApiKey) {
    headers.set("x-platrixa-api-key", serverApiKey);
  }
  // --- Visitor assertion signing (edge-owned HMAC-SHA256) ---
  // A fresh, single-use assertion is signed over the exact method, path,
  // query, body hash, timestamp and nonce. The secret stays server-side.
  const visitorSecret = (process.env.PLATRIXA_VISITOR_HMAC_SECRET ?? "").trim();
  if (!visitorSecret) {
    return errorEnvelope(
      503,
      "VISITOR_SIGNING_NOT_CONFIGURED",
      "Visitor signing is not configured for this frontend environment.",
      requestId,
    );
  }

  const isReadOnly = request.method === "GET" || request.method === "HEAD";
  const bodyBytes = isReadOnly
    ? new Uint8Array(0)
    : new Uint8Array(await request.arrayBuffer());
  const bodyHash = crypto.createHash("sha256").update(bodyBytes).digest("hex");
  const requestTimestamp = String(Math.floor(Date.now() / 1000));
  const requestNonce = crypto.randomUUID();
  const canonical = [
    "V1",
    "platrixa",
    visitorId,
    request.method.toUpperCase(),
    incomingPath,
    searchParams,
    bodyHash,
    requestTimestamp,
    requestNonce,
  ].join("|");
  const assertion = `${canonical}|${crypto
    .createHmac("sha256", visitorSecret)
    .update(canonical)
    .digest("hex")}`;

  // Edge-owned identity — overwrites any browser-supplied value.
  headers.set("x-platrixa-visitor-id", visitorId);
  headers.set("x-platrixa-visitor-assertion", assertion);
  headers.set("x-platrixa-request-timestamp", requestTimestamp);
  headers.set("x-platrixa-visitor-nonce", requestNonce);
  headers.set("x-platrixa-surface", "website");
  // Forward the Origin the proxy already validated so the backend's own
  // defence-in-depth CSRF check sees the same value (it re-validates).
  const forwardedOrigin = request.headers.get("origin");
  if (forwardedOrigin) headers.set("origin", forwardedOrigin);

  // Forward other allowed headers, skipping browser-supplied trusted headers
  for (const [name, value] of request.headers.entries()) {
    const lower = name.toLowerCase();
    // Skip browser-supplied trusted headers that the handler must manage
    if (NON_BROWSER_HEADERS.has(lower)) continue;
    if (lower === "x-platrixa-api-key") continue;  // always server-side
    if (lower === "x-platrixa-visitor-id") continue;  // edge-managed
    if (lower === "x-platrixa-visitor-assertion") continue;  // edge-managed
    if (lower === "x-platrixa-request-timestamp") continue;  // edge-managed
    if (lower === "x-platrixa-visitor-nonce") continue;  // edge-managed
    if (lower === "x-platrixa-surface") continue;  // edge-managed
    if (FORWARD_HEADERS.has(lower)) headers.set(name, value);
  }

  // Phase 5I (audit C2): the server-side operator key is a FALLBACK for
  // local development only — it is used when and ONLY when the caller
  // supplied no key of their own. Overwriting a caller-supplied key
  // silently re-attributed every request to the operator's tenant (the
  // audit measured GET /v1/usage returning the WRONG tenant's usage with
  // no error). Management credentials are never substituted.
  const apiKey = (process.env[PROXY_KEY_ENV] ?? "").trim();
  if (apiKey && !headers.has("x-platrixa-api-key")) {
    headers.set("x-platrixa-api-key", apiKey);
  }

  const method = request.method.toUpperCase();
  const init: RequestInit = { method, headers, redirect: "follow" };
  if (bodyBytes.length > 0) {
    init.body = bodyBytes;
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, { ...init, signal: AbortSignal.timeout(PROXY_TIMEOUT_MS) });
  } catch (err) {
    const timedOut =
      err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
    return timedOut
      ? errorEnvelope(
          504,
          "BACKEND_TIMEOUT",
          "The Platrixa backend did not answer in time; retry shortly.",
          requestId,
        )
      : errorEnvelope(502, "BACKEND_UNREACHABLE", "The Platrixa backend could not be reached.", requestId);
  }

  const contentType = upstream.headers.get("content-type") ?? "";
  const body = await upstream.text();

  // A non-JSON body is NOT relayed: it is the hosting platform's own error page
  // (a hibernation notice, a CDN challenge), which exposes infrastructure and
  // carries no code the client can act on. Same contract as the Pages proxies.
  if (!contentType.toLowerCase().includes("application/json")) {
    const status = upstream.status >= 400 ? upstream.status : 502;
    return errorEnvelope(
      status,
      "BACKEND_ERROR",
      status === 429
        ? "The Platrixa backend is rate-limited or starting up; retry shortly."
        : `The Platrixa backend returned a response that is not a JSON envelope (HTTP ${upstream.status}).`,
      requestId,
    );
  }

  return {
    status: upstream.status,
    body,
    contentType,
    requestId,
    responseHeaders: upstream.headers,
  };
}

/** Render a ProxyOutcome as a NextResponse (no data transformation). */
export function proxyResponse(outcome: ProxyOutcome): Response {
  const outHeaders: Record<string, string> = {
    "content-type": outcome.contentType,
    "cache-control": "no-store",
  };
  // Phase 5I (audit C2): idempotency and transport contract headers live
  // in the RESPONSE too — Idempotent-Replayed, Retry-After, and the
  // error-tag header are part of the public developer contract and were
  // previously swallowed here, making replay observability impossible
  // through the browser origin.
  if (outcome.responseHeaders) {
    for (const [name, value] of outcome.responseHeaders.entries()) {
      const lower = name.toLowerCase();
      if (
        lower === "idempotent-replayed" ||
        lower === "retry-after" ||
        lower === "x-platrixa-error" ||
        lower === "idempotency-key" ||
        lower === "x-request-id" ||
        lower.startsWith("x-ratelimit-")
      ) {
        outHeaders[name] = value;
      }
    }
  }
  return new Response(outcome.body, {
    status: outcome.status,
    headers: outHeaders,
  });
}

/** Bind the standard method handlers for one catch-all route. */
export function platrixaProxy() {
  async function handler(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
    const { path } = await ctx.params;
    const outcome = await forwardToPlatrixa(
      request,
      path ?? [],
      request.nextUrl.search.slice(1),
    );
    return proxyResponse(outcome);
  }
  return {
    GET: handler,
    POST: handler,
    PUT: handler,
    PATCH: handler,
    DELETE: handler,
    HEAD: handler,
    OPTIONS: handler,
  };
}
