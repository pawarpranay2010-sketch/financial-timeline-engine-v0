# Visitor Ownership V2 — Runtime configuration

This document lists every runtime component that reads the V2 visitor
configuration, the exact value each one needs, and what happens when it is
missing. No production setting is changed by this document; it is the
checklist for whoever configures each environment.

## Variables

| Variable | Purpose | Shared by |
|---|---|---|
| `PLATRIXA_PUBLIC_ORIGIN` | Exact public website origin used for CSRF Origin checks, e.g. `https://app.example.com` (scheme + host, no path, no trailing slash) | Cloudflare `/api`, Cloudflare `/v1`, Next.js proxy, Railway backend |
| `PLATRIXA_VISITOR_HMAC_SECRET` | HMAC-SHA256 secret used to sign (edge) and verify (backend) visitor assertions. High-entropy, server-side only, identical on both sides | Cloudflare `/api`, Cloudflare `/v1`, Next.js proxy, Railway backend |
| `PLATRIXA_API_KEY` | Server-side website API key injected by the edge; the browser never supplies it | Cloudflare `/api`, Cloudflare `/v1`, Next.js proxy |
| `PLATRIXA_METERING_DATABASE_URL` | PostgreSQL store holding `visitor_assertion_nonces` (and jobs) | Railway backend |
| `PLATRIXA_MANAGEMENT_ASSERTION_SECRET` | Management gateway assertion secret (unchanged) | Railway backend |
| `PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS` | Comma-separated **exact** issuer allowlist for management gateway assertions. Unset/empty/malformed = every gateway assertion is rejected | Railway backend (+ the signing gateway must use one of these values) |

## Where each value is read

| Component | File | Reads |
|---|---|---|
| Cloudflare Pages `/api` function | `frontend/functions/api/[[path]].js` | `env.PLATRIXA_PUBLIC_ORIGIN`, `env.PLATRIXA_VISITOR_HMAC_SECRET`, `env.PLATRIXA_API_KEY` |
| Cloudflare Pages `/v1` function | `frontend/functions/v1/[[path]].js` | same three |
| Next.js dev/preview proxy | `frontend/web/lib/platrixa-proxy.ts` | `process.env.PLATRIXA_PUBLIC_ORIGIN`, `PLATRIXA_VISITOR_HMAC_SECRET`, `PLATRIXA_API_KEY` |
| Railway backend middleware | `api/main.py` (`_visitor_scope_and_csrf`) | `PLATRIXA_PUBLIC_ORIGIN`; verification uses `PLATRIXA_VISITOR_HMAC_SECRET` via `backend/auth/api_keys.py` |

## Required configuration per platform

### Cloudflare Pages Functions
Set all three as **encrypted environment variables / secrets** on the Pages
project (Production and Preview as appropriate):

- `PLATRIXA_PUBLIC_ORIGIN` = exact public origin of the site
- `PLATRIXA_VISITOR_HMAC_SECRET` = same value as Railway
- `PLATRIXA_API_KEY` = the Phase 15 website API key

### Next.js server proxy (local/preview only)
Set in the server environment (not `NEXT_PUBLIC_*`, so it never reaches the
bundle):

- `PLATRIXA_API_BASE_URL`, `PLATRIXA_PUBLIC_ORIGIN`,
  `PLATRIXA_VISITOR_HMAC_SECRET`, `PLATRIXA_API_KEY`

### Railway backend
- `PLATRIXA_PUBLIC_ORIGIN` must equal the browser-facing origin exactly
- `PLATRIXA_VISITOR_HMAC_SECRET` must equal the edge value byte-for-byte
- `PLATRIXA_METERING_DATABASE_URL` must point at the store that has had
  `backend/database/platrixa_visitor_nonce_schema.sql` applied

## Fail-closed behavior

| Missing / invalid | Result |
|---|---|
| Edge `PLATRIXA_VISITOR_HMAC_SECRET` missing | Proxy returns 503; **no request is forwarded** |
| Edge `PLATRIXA_PUBLIC_ORIGIN` missing | Browser mutation gets 403 |
| Backend `PLATRIXA_VISITOR_HMAC_SECRET` missing | `VISITOR_AUTH_DISABLED` → 403, no scope bound |
| Backend `PLATRIXA_PUBLIC_ORIGIN` missing | Browser mutation on the website surface gets 403 |
| `visitor_assertion_nonces` unreachable | `NONCE_STORE_UNAVAILABLE` → 403, no scope bound |
| Assertion missing / unsigned / stale / replayed / mismatched body | 403 `VISITOR_AUTH_REQUIRED`, no scope bound |

A website request (`x-platrixa-surface: website`) can **never** fall back to
tenant-only access. Developer API and worker requests never carry the surface
marker, so they keep their existing tenant-scoped authorization.

## Management gateway: issuer allowlist and replay protection

Management gateway assertions are operator credentials and are verified
**independently** of visitor assertions (separate secret, separate issuer
allowlist, separate nonce table). Visitor identity headers are never accepted
by management authorization.

### `PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS`

- Format: comma-separated exact issuer identifiers, whitespace trimmed, e.g.
  `PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS=platrixa-operator-v1`
- Allowed characters per issuer: `A-Z a-z 0-9 . _ : -`, 1–128 chars, starting
  with an alphanumeric character. No spaces inside an issuer.
- Matching is **exact only** — no substring, prefix or wildcard matching.
- An unset, empty, or malformed allowlist **fails closed**: every assertion is
  rejected. There is no implicit default issuer.
- The HMAC secret (`PLATRIXA_MANAGEMENT_ASSERTION_SECRET`) is separate from the
  allowlist and is never derived from it.
- The signing gateway and the backend must be configured with the *same*
  intended issuer value; a typo on either side yields 403s, by design.
- The legitimate operator issuer established by the existing gateway is
  `platrixa-operator-v1` (see `PLATRIXA_FINAL_SECURITY_AUDIT_REPORT.md`).

### Rotating issuers safely

1. Add the new issuer **alongside** the old one:
   `PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS=platrixa-operator-v1,platrixa-operator-v2`
   and redeploy the backend. Both issuers are now accepted.
2. Switch the signing gateway to the new issuer (`platrixa-operator-v2`).
3. Verify management traffic succeeds (no `API_KEY_MANAGEMENT_UNAUTHORIZED` in
   logs) for the whole rotation window.
4. Remove the old issuer:
   `PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS=platrixa-operator-v2` and redeploy.

Never remove the old value before traffic is confirmed on the new one, and
never leave a stale issuer trusted longer than the rotation window.

### Replay protection

Every accepted assertion consumes `(issuer, SHA-256(nonce))` in
`management_assertion_nonces` (composite primary key) before it is accepted:

- Stored: issuer, nonce **digest**, `expires_at` (= assertion timestamp + 600s),
  `created_at`. The raw nonce is never persisted.
- A second use of the same `(issuer, nonce)` is rejected as a replay; concurrent
  duplicates can never both succeed (database uniqueness, not application
  timing).
- If the nonce store is unset, unreachable, or the insert fails, the request is
  rejected — never failed open.
- The nonce is consumed only after signature, audience, issuer, timestamp,
  tenant and nonce-format checks pass, so invalid assertions cannot burn a
  valid nonce.
- Apply the table with
  `python -m backend.auth.init_management_nonce` (see “Order of operations”).

## Visitor nonce cleanup (scheduled maintenance)

`visitor_assertion_nonces` rows expire one hour after use but are not removed
automatically. Run the bounded one-shot cleanup from any existing scheduler
(cron, platform scheduled job, CI schedule) — do **not** add a new service:

```bash
PLATRIXA_METERING_DATABASE_URL=postgresql://... \
  python -m backend.auth.visitor_nonces --batch-size 1000
```

- Deletes only rows with `expires_at < NOW()`, at most `batch_size` per run
  (1…10 000 default 1000).
- Safe for concurrent runs (`FOR UPDATE SKIP LOCKED`); returns and prints the
  number of rows actually deleted.
- Exit status `0` = ran (check the printed count), `1` = cleanup failed; the
  failure is logged and authentication still fails closed.
- Example cron entry (hourly):
  `17 * * * * cd /app && PLATRIXA_METERING_DATABASE_URL=... python -m backend.auth.visitor_nonces --batch-size 1000`

## Order of operations

1. Apply `backend/database/platrixa_visitor_schema.sql` (job ownership columns).
2. Apply `backend/database/platrixa_visitor_nonce_schema.sql` (replay store).
3. Apply `backend/database/platrixa_management_nonce_schema.sql`
   (`python -m backend.auth.init_management_nonce`) if the management gateway
   is used.
4. Set `PLATRIXA_VISITOR_HMAC_SECRET` identically in the edge and Railway.
5. Set `PLATRIXA_PUBLIC_ORIGIN` in the edge and Railway.
6. Set `PLATRIXA_MANAGEMENT_TRUSTED_ISSUERS` on the backend (and the same
   issuer on the signing gateway).
7. Schedule `python -m backend.auth.visitor_nonces` with an existing scheduler.
8. Only then route website traffic through the V2 proxies.
