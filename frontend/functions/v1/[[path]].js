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
//   - Without API_BACKEND_URL it fails fast with the same explicit 502 JSON
//     body instead of Cloudflare's generic 405 — no backend URL is invented.
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
  if (!url.pathname.startsWith("/v1/") && url.pathname !== "/v1") {
    return new Response(
      JSON.stringify({
        error: "path_not_forwarded",
        detail: "Only the /v1/* namespace is proxied by this function.",
      }),
      {
        status: 404,
        headers: { "content-type": "application/json" },
      }
    );
  }

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
