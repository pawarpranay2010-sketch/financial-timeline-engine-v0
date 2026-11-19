/**
 * Cloudflare Pages build for the Next.js frontend (frontend/web).
 *
 * Production target: Cloudflare Pages static hosting + the EXISTING Pages
 * Functions (functions/api/[[path]].js and functions/v1/[[path]].js) as the
 * server-side API boundary toward the FastAPI backend.
 *
 * Why: Next.js route handlers cannot be statically exported (only static
 * GETs can), so for this build the two Node proxy handlers are excluded and
 * `output: "export"` is enabled via PLATRIXA_STATIC_EXPORT=1. They are
 * ALWAYS restored afterwards — the managed dev/preview workflow keeps using
 * `npm run build` (full server, proxy included); this script affects only
 * the Pages artifact in ./out.
 *
 * Cloudflare discovers Functions only inside the Pages build output
 * directory, so functions/ is copied into out/ as the final step
 * (DEPLOYMENT.md, "Pages proxy" section).
 */
import { cpSync, existsSync, mkdirSync, renameSync, rmSync } from "node:fs";
import { execSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(scriptDir, ".."); // frontend/web
const functionsDir = path.resolve(root, "..", "functions"); // frontend/functions

const proxyRoutes = [
  path.join(root, "app", "api", "[...path]"),
  path.join(root, "app", "v1", "[...path]"),
];
const stashDir = path.join(root, ".static-export-stash");

for (const dir of proxyRoutes) {
  if (!existsSync(dir)) {
    console.error(`build-pages: expected proxy route missing: ${dir}`);
    process.exit(1);
  }
}

// 1. Hide the Node route handlers for this build only.
if (existsSync(stashDir)) rmSync(stashDir, { recursive: true, force: true });
mkdirSync(stashDir);
for (const dir of proxyRoutes) {
  const name = `${path.basename(path.dirname(dir))}-${path.basename(dir)}`;
  renameSync(dir, path.join(stashDir, name));
}
console.log("build-pages: proxy route handlers excluded for static export");

let failed = false;
try {
  // 2. Static export (next.config.ts reads PLATRIXA_STATIC_EXPORT).
  execSync("npx next build", {
    stdio: "inherit",
    cwd: root,
    env: { ...process.env, PLATRIXA_STATIC_EXPORT: "1" },
  });
} catch {
  failed = true;
} finally {
  // 3. Restore the handlers no matter what — dev/preview mode must survive.
  for (const dir of proxyRoutes) {
    const name = `${path.basename(path.dirname(dir))}-${path.basename(dir)}`;
    const stashed = path.join(stashDir, name);
    if (existsSync(stashed)) renameSync(stashed, dir);
  }
  rmSync(stashDir, { recursive: true, force: true });
  console.log("build-pages: proxy route handlers restored");
}
if (failed) {
  console.error("build-pages: static export failed — out/ may be stale");
  process.exit(1);
}

// 4. Package the proxy handlers with the artifact.
//
// A `functions/` directory is only compiled by Cloudflare's Git build, so a
// direct `wrangler pages deploy` of out/ would serve the site and answer
// /api/* and /v1/* with the static 404 page. Ship the SAME handlers as an
// advanced-mode _worker.js (pages-worker.js) that imports them instead, so
// both publish paths expose the identical API boundary.
// A stale out/functions from an older build would collide with _worker.js
// (Pages rejects an artifact that has both), so it is removed.
rmSync(path.join(root, "out", "functions"), { recursive: true, force: true });
const proxyDir = path.join(root, "out", "_proxy");
mkdirSync(proxyDir, { recursive: true });
cpSync(path.join(functionsDir, "api", "[[path]].js"), path.join(proxyDir, "api.js"));
cpSync(path.join(functionsDir, "v1", "[[path]].js"), path.join(proxyDir, "v1.js"));
cpSync(path.join(root, "pages-worker.js"), path.join(root, "out", "_worker.js"));
console.log(
  "build-pages: Pages artifact ready at frontend/web/out (static app + _worker.js proxy)",
);
