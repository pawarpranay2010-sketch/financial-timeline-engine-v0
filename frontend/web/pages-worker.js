// Platrixa — Cloudflare Pages advanced-mode worker for the static artifact.
// ---------------------------------------------------------------------
// `wrangler pages deploy` uploads a directory of static assets; it does not
// compile a `functions/` directory (only Cloudflare's Git build does). A
// direct upload of `out/` therefore served the site but answered /api/* and
// /v1/* with the static 404 page, silently dropping the same-origin API
// boundary.
//
// This worker is the deployment adapter: it mounts the EXISTING, unmodified
// proxy handlers from `frontend/functions/` (packaged by build-pages.mjs as
// out/_proxy/*.js) and falls through to the static assets for everything
// else. The handlers still own every security decision — server-side key
// injection, edge-owned visitor identity and the signed assertion — so no
// proxy logic is duplicated or weakened here.
import { onRequest as apiProxy } from "./_proxy/api.js";
import { onRequest as v1Proxy } from "./_proxy/v1.js";

const PROXY_ROUTES = [
  ["/api", apiProxy],
  ["/v1", v1Proxy],
];

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);

    for (const [prefix, handler] of PROXY_ROUTES) {
      if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
        return handler({
          request,
          env,
          params: {},
          data: {},
          next: () => env.ASSETS.fetch(request),
          waitUntil: (promise) => ctx.waitUntil(promise),
        });
      }
    }

    return env.ASSETS.fetch(request);
  },
};
