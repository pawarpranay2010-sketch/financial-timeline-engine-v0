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
  "authorization",
  "accept-language",
  "x-requested-with",
];

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

  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const value = context.request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const init = { method: context.request.method, headers, redirect: "follow" };
  if (context.request.method !== "GET" && context.request.method !== "HEAD") {
    init.body = await context.request.arrayBuffer();
  }

  const upstream = await fetch(target, init);

  const respHeaders = new Headers(upstream.headers);
  respHeaders.set(
    "content-type",
    upstream.headers.get("content-type") || "application/json"
  );
  return new Response(upstream.body, {
    status: upstream.status,
    headers: respHeaders,
  });
}