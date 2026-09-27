import type { NextConfig } from "next";

/**
 * Two build modes:
 *
 *  - default (dev server / `next start` on the managed preview): full Next
 *    server including the same-origin route-handler proxy in
 *    app/api/[...path] and app/v1/[...path] (PLATRIXA_API_BASE_URL and
 *    PLATRIXA_API_KEY stay server-side).
 *
 *  - PLATRIXA_STATIC_EXPORT=1 (Cloudflare Pages production build): static
 *    export to ./out. The browser talks to the same-origin Cloudflare Pages
 *    Functions (functions/api/[[path]].js + functions/v1/[[path]].js)
 *    instead; the Node proxy handlers cannot be statically exported and are
 *    excluded from this build only by scripts/build-pages.mjs.
 */
const isStaticExport = process.env.PLATRIXA_STATIC_EXPORT === "1";

const nextConfig: NextConfig = {
  ...(isStaticExport ? { output: "export" as const } : {}),
  // Required by `output: "export"`; the app currently uses no next/image.
  images: { unoptimized: true },
};

export default nextConfig;
