/**
 * Private owner dashboard API — server-side only.
 *
 * AUTHENTICATION IS TWO-LAYER AND BOTH ARE SERVER-SIDE:
 *   1. HTTP Basic auth against PLATRIXA_OWNER_DASHBOARD_USER/PASSWORD. If the
 *      password is unset the route fails CLOSED (503), never open. The
 *      browser keeps the credential; it is never in page JavaScript, browser
 *      storage, or a URL.
 *   2. A freshly signed management gateway assertion per request — the same
 *      credential the operator ingress uses. The signing secret stays here.
 *
 * Only `/v1/owner/onboarding/*` is ever addressed: this is not a general
 * proxy, and the target origin comes exclusively from PLATRIXA_API_BASE_URL.
 */

import crypto from "node:crypto";
import type { NextRequest } from "next/server";
import { managementAssertionHeaders } from "@/lib/management-assertion";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const TIMEOUT_MS = 25_000;

function secureEqual(a: string, b: string): boolean {
  const da = crypto.createHash("sha256").update(a).digest();
  const db = crypto.createHash("sha256").update(b).digest();
  return crypto.timingSafeEqual(da, db);
}

function unauthorized(reason: string): Response {
  return new Response(
    JSON.stringify({ error: { code: "OWNER_UNAUTHENTICATED", message: reason } }),
    {
      status: 401,
      headers: {
        "content-type": "application/json",
        "cache-control": "no-store",
        "www-authenticate": 'Basic realm="Platrixa owner", charset="UTF-8"',
      },
    },
  );
}

function ownerAuthorized(request: NextRequest): boolean {
  const expectedUser = (process.env.PLATRIXA_OWNER_DASHBOARD_USER || "owner").trim();
  const expectedPassword = (process.env.PLATRIXA_OWNER_DASHBOARD_PASSWORD || "").trim();
  if (!expectedPassword) return false; // fail closed: no password configured
  const header = request.headers.get("authorization") || "";
  if (!header.startsWith("Basic ")) return false;
  let decoded: string;
  try {
    decoded = Buffer.from(header.slice(6), "base64").toString("utf8");
  } catch {
    return false;
  }
  const separator = decoded.indexOf(":");
  if (separator < 0) return false;
  const user = decoded.slice(0, separator);
  const password = decoded.slice(separator + 1);
  return secureEqual(user, expectedUser) && secureEqual(password, expectedPassword);
}

function json(status: number, body: string, extra?: Record<string, string>): Response {
  return new Response(body, {
    status,
    headers: {
      "content-type": "application/json",
      "cache-control": "no-store",
      ...(extra || {}),
    },
  });
}

async function handle(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  if (!ownerAuthorized(request)) {
    return unauthorized("Owner credentials are required.");
  }

  const base = (process.env.PLATRIXA_API_BASE_URL || "").trim().replace(/\/+$/, "");
  const secret = (process.env.PLATRIXA_MANAGEMENT_ASSERTION_SECRET || "").trim();
  const issuer = (process.env.PLATRIXA_MANAGEMENT_ASSERTION_ISSUER || "").trim();
  const tenantId = (process.env.PLATRIXA_KEY_MANAGEMENT_TENANT_ID || "").trim();
  if (!base || !secret || !issuer || !tenantId) {
    return json(
      503,
      JSON.stringify({
        error: {
          code: "OWNER_DASHBOARD_NOT_CONFIGURED",
          message:
            "The dashboard requires PLATRIXA_API_BASE_URL, PLATRIXA_MANAGEMENT_ASSERTION_SECRET, " +
            "PLATRIXA_MANAGEMENT_ASSERTION_ISSUER and PLATRIXA_KEY_MANAGEMENT_TENANT_ID.",
        },
      }),
    );
  }

  const { path } = await ctx.params;
  const segments = (path || []).filter((segment) => segment.length > 0);
  if (segments.some((segment) => segment === ".." || segment.includes("/"))) {
    return json(404, JSON.stringify({ error: { code: "NOT_FOUND", message: "not found" } }));
  }

  const queryString = request.nextUrl.search.startsWith("?")
    ? request.nextUrl.search.slice(1)
    : request.nextUrl.search;
  const backendPath = `/v1/owner/onboarding/${segments.join("/")}`;
  const method = request.method.toUpperCase();
  const body =
    method === "GET" || method === "HEAD"
      ? new Uint8Array(0)
      : new Uint8Array(await request.arrayBuffer());

  let target: URL;
  try {
    target = new URL(base + backendPath + (queryString ? `?${queryString}` : ""));
  } catch {
    return json(500, JSON.stringify({ error: { code: "BACKEND_NOT_CONFIGURED", message: "invalid base URL" } }));
  }

  const headers = new Headers({
    "content-type": "application/json",
    "x-request-id": crypto.randomUUID(),
  });
  for (const [name, value] of Object.entries(
    managementAssertionHeaders({
      secret,
      issuer,
      tenantId,
      method,
      path: target.pathname,
      queryString,
      contentType: "application/json",
      body,
    }),
  )) {
    headers.set(name, value);
  }

  let upstream: Response;
  try {
    upstream = await fetch(target.toString(), {
      method,
      headers,
      body: body.length > 0 ? body : undefined,
      redirect: "manual",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (error) {
    const timedOut =
      error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
    return json(
      timedOut ? 504 : 502,
      JSON.stringify({
        error: {
          code: timedOut ? "BACKEND_TIMEOUT" : "BACKEND_UNREACHABLE",
          message: timedOut ? "The backend did not answer in time." : "The backend could not be reached.",
        },
      }),
    );
  }

  const text = await upstream.text();
  const relay: Record<string, string> = {};
  for (const name of ["x-platrixa-error", "retry-after"]) {
    const value = upstream.headers.get(name);
    if (value) relay[name] = value;
  }
  return json(upstream.status, text, relay);
}

export async function GET(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(request, ctx);
}

export async function POST(request: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  return handle(request, ctx);
}
