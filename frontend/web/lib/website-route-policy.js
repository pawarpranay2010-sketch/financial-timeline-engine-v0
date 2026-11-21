/**
 * Platrixa — explicit public-website API route policy (single source of truth).
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The public website reaches the backend through a same-origin proxy
 * (Cloudflare Pages `functions/v1/[[path]].js` in production, this Next
 * route handler in dev/preview). That proxy authenticates the browser as
 * the shared website tenant, so any `/v1/*` path it forwards becomes
 * reachable "as the website". Forwarding the whole namespace let a
 * same-origin script call tenant-scoped management routes (key rotation,
 * tenant job history) with the shared credential.
 *
 * The fix is an explicit, default-deny route policy: only the endpoints the
 * website actually uses are forwarded, matched by METHOD and EXACT
 * normalized path. Everything else — key rotation, key reveal other than
 * the token-gated page, developer/owner management, `/v1/internal/*` — is
 * never forwarded by the public proxy.
 *
 * This module is the canonical table. `frontend/functions/v1/[[path]].js`
 * carries an identical copy (Pages Functions cannot import outside the
 * functions directory), and `tests/test_onboarding_remediation.py` asserts
 * the two tables are identical, so they cannot drift.
 *
 * NORMALIZATION (defence in depth against path-confusion)
 * ------------------------------------------------------
 * A request is rejected unless its raw pathname is already exactly the
 * canonical form: absolute, no percent-encoding, no backslash, no `//`,
 * no `.`/`..` segments, no trailing slash, no control/space characters.
 * That means `%2F`, `%2e%2e`, `//`, `/./`, `/x/../` and trailing-slash
 * variants never reach an allowlisted pattern.
 */

/** @typedef {{ methods: string[], pattern: string }} WebsiteRouteSpec */

/** @type {WebsiteRouteSpec[]} */
export const WEBSITE_ROUTE_SPECS = [
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
  // Token-gated one-time key retrieval: the page the onboarding email links
  // to. The token IS the credential (32 random bytes, single use, digest
  // stored); the request consumes no tenant authority. Deliberately kept.
  { methods: ["POST"], pattern: "^/v1/developer/keys/reveal$" },

  // DELIBERATELY ABSENT — never reachable from the public website:
  //   POST /v1/developer/keys/rotate      (shared-tenant credential rotation)
  //   GET  /v1/developer/jobs             (tenant-scoped job history)
  //   any  /v1/developer/api-keys/*       (management plane)
  //   any  /v1/developer/usage|requests   (management plane)
  //   any  /v1/owner/*                    (owner dashboard)
  //   any  /v1/operator/*                 (operator ingress)
  //   any  /v1/internal/*                 (operational endpoints)
];

/** @type {{ methods: string[], pattern: RegExp }[]} */
export const WEBSITE_ROUTES = WEBSITE_ROUTE_SPECS.map((spec) => ({
  methods: spec.methods,
  pattern: new RegExp(spec.pattern),
}));

export const MAX_WEBSITE_PATH_LENGTH = 256;

/**
 * Canonical pathname, or null when the request is not already canonical.
 * @param {unknown} rawPathname
 * @returns {string | null}
 */
export function normalizeWebsitePath(rawPathname) {
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

/**
 * Default-deny: true only for an allowlisted method + exact canonical path.
 * @param {unknown} method
 * @param {unknown} pathname
 * @returns {boolean}
 */
export function isAllowedWebsiteRoute(method, pathname) {
  const normalized = normalizeWebsitePath(pathname);
  if (normalized === null) return false;
  const upper = String(method || "").toUpperCase();
  return WEBSITE_ROUTES.some(
    (route) => route.methods.includes(upper) && route.pattern.test(normalized)
  );
}
