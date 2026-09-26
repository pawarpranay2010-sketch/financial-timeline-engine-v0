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

import type { NextRequest } from "next/server";

export const PROXY_BASE_ENV = "PLATRIXA_API_BASE_URL";
export const PROXY_KEY_ENV = "PLATRIXA_API_KEY";

/** The only path namespaces this proxy will ever forward. */
const FORWARDED_PREFIXES = ["/api/", "/v1/"] as const;

/** Headers safe to forward browser→backend (mirrors the CF Pages allowlist). */
const FORWARD_HEADERS = new Set([
  "content-type",
  "accept",
  "accept-language",
  "x-requested-with",
  "x-request-id",
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

  const headers = new Headers();
  for (const [name, value] of request.headers.entries()) {
    const lower = name.toLowerCase();
    if (NON_BROWSER_HEADERS.has(lower)) continue;
    if (FORWARD_HEADERS.has(lower)) headers.set(name, value);
  }

  const apiKey = (process.env[PROXY_KEY_ENV] ?? "").trim();
  if (apiKey) headers.set("x-platrixa-api-key", apiKey);

  const method = request.method.toUpperCase();
  const init: RequestInit = { method, headers, redirect: "follow" };
  if (method !== "GET" && method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return errorEnvelope(502, "BACKEND_UNREACHABLE", "The Platrixa backend could not be reached.", requestId);
  }

  const body = await upstream.text();
  return {
    status: upstream.status,
    body,
    contentType: upstream.headers.get("content-type") ?? "application/json",
    requestId,
  };
}

/** Render a ProxyOutcome as a NextResponse (no data transformation). */
export function proxyResponse(outcome: ProxyOutcome): Response {
  return new Response(outcome.body, {
    status: outcome.status,
    headers: {
      "content-type": outcome.contentType,
      "cache-control": "no-store",
    },
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
