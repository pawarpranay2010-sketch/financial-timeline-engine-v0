# Developer API — self-service onboarding (v1)

A developer can apply on the website, verify a work email, and — when the
server-side eligibility rules say so — receive a dedicated tenant, one API key
and an initial monthly quota automatically. The owner is notified by email and
sees the same events in a private dashboard.

This document describes what is implemented. Anything not implemented is
marked as such rather than described as working.

## Flow

```
POST /v1/developer/access-requests   (public, website surface)
        │  validate · normalise · rate-limit · dedupe
        ▼
PENDING_VERIFICATION ── verification email (single-use token) ──► inbox
        │  POST /v1/developer/verify-email (token, single use)
        ▼
VERIFIED ── eligibility (server-side only) ──┬──► PROVISIONING ──► ACTIVE
                                             └──► REVIEW_REQUIRED
        │  one transaction: tenant key row + quota row + delivery row
        │                   + application state + notifications + audit
        ▼
one-time key link (short TTL) ── POST /v1/developer/keys/reveal ──► key shown once
```

`EXPIRED` (abandoned applications) and `SUSPENDED` (owner action) complete the
closed state vocabulary. Status is **never** read from a request field.

## Endpoints

Public (no API key; the website surface requires the edge-signed visitor
assertion and a trusted `Origin`, enforced by `api/main.py` middleware):

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/developer/access-requests` | six-field application; 202; identical response for new/duplicate |
| POST | `/v1/developer/verify-email` | consumes a single-use token |
| POST | `/v1/developer/resend-verification` | throttled; non-enumerating |
| GET | `/v1/developer/access-status` | requires BOTH `application_id` and the applied `email` |

Developer (authenticated by the tenant's own data-plane key; tenant resolved
from the key, never from input):

| Method | Path | Notes |
|---|---|---|
| POST | `/v1/developer/keys/reveal` | one-time retrieval by opaque link token; 410 on reuse/expiry |
| POST | `/v1/developer/keys/rotate` | revokes current key and issues replacement in one transaction |
| GET | `/v1/developer/jobs` | tenant-scoped job history (`limit`, `offset`) |

Usage reuses the existing contract rather than adding a third route:
`GET /v1/usage` (data-plane key, zero quota cost) and the owner's
`GET /v1/developer/usage` (management plane).

Owner (management plane — signed gateway assertion; a developer key cannot
produce one):

| Method | Path |
|---|---|
| GET | `/v1/owner/onboarding/overview` |
| GET | `/v1/owner/onboarding/applications` (`status`, `tenant_id`, `limit`, `offset`) |
| GET | `/v1/owner/onboarding/applications/{id}` |
| POST | `/v1/owner/onboarding/applications/{id}/advance` · `/provision` · `/suspend` · `/restore` · `/reject` |
| POST | `/v1/owner/onboarding/tenants/{tenant_id}/quota` |
| POST | `/v1/owner/onboarding/tenants/{tenant_id}/keys/{key_id}/revoke` |
| POST | `/v1/owner/onboarding/tenants/{tenant_id}/keys/rotate` |
| GET | `/v1/owner/onboarding/notifications` · POST `/notifications/dispatch` |
| POST | `/v1/owner/onboarding/maintenance/expire` |

## Key handling

* Keys are **derived**, never stored: `raw = "plx_live_" + base64url(HMAC-SHA256(secret, key_id|nonce))`.
  The credential tables receive only `SHA-256(raw)`; the delivery row holds the
  public `key_id` and a random nonce.
* On reveal the server re-derives the key, checks the digest, returns it once,
  and **zeroes the nonce** — after that the key cannot be re-derived by anyone,
  while the stored digest still authenticates it.
* Verification and reveal tokens are random 32-byte values; only their SHA-256
  digests are stored in the verification/delivery tables.
  *Residual*: the outbound message body necessarily carries the link until it
  is delivered; bodies are cleared when a message reaches `SENT`/`FAILED`.
* Rotation revokes the old key and deactivates its quota row in the same
  transaction, so there is no overlap window.

## Eligibility (default policy)

Automatic provisioning requires all of: verified work email; complete
application; no existing tenant for the derived tenant id; no disposable
domain; no role account (`admin@`, `noreply@`, …); a non-free-mail domain
unless `PLATRIXA_ONBOARDING_ALLOW_FREEMAIL=1`; within the per-client and global
onboarding caps; and a configured key-delivery secret. Anything else becomes
`REVIEW_REQUIRED` with a recorded reason, and the owner is notified. A missing
delivery secret holds the application in `VERIFIED` — it never half-provisions.

## Quota

`PLATRIXA_ONBOARDING_INITIAL_QUOTA` (default **50**) units, where one unit is
one **admitted** data-plane request in the UTC `YYYY-MM` bucket — exactly the
existing `platrixa_tenant_quotas` semantics. Authentication failures cost
nothing. Each tenant's quota stays independently configurable via the owner
route.

## Configuration

| Variable | Purpose |
|---|---|
| `PLATRIXA_METERING_DATABASE_URL` (or `PLATRIXA_ONBOARDING_DATABASE_URL`) | store for onboarding + metering |
| `PLATRIXA_KEY_DELIVERY_SECRET` | ≥32 chars; required to issue keys (no default) |
| `PLATRIXA_KEY_DELIVERY_SECRET_VERSION` / `_PREVIOUS` | rotate the derivation secret without stranding one-time links: bump the version, keep the old value in `_PREVIOUS` until outstanding deliveries are revealed |
| `PLATRIXA_ONBOARDING_DISPATCH_TOKEN` | shared secret for `POST /v1/internal/onboarding/dispatch` and `GET .../health`; unset ⇒ both answer 404 |
| `PLATRIXA_ONBOARDING_DISPATCH_LEASE_SECONDS` | how long one dispatcher owns a row before another may recover it (default **300**); renewed for the whole send, so it is a crash-detection window, not a send timeout |
| `PLATRIXA_ONBOARDING_AUTO_MIGRATE` | **dev/test only**; the runtime never runs DDL, so leave unset/0 and run the init script |
| `PLATRIXA_CLIENT_IP_HEADER` / `PLATRIXA_TRUSTED_PROXY_COUNT` | trusted client identity for per-IP caps (see below) |
| `PLATRIXA_ONBOARDING_ENABLED` | default on |
| `PLATRIXA_ONBOARDING_INITIAL_QUOTA` | default 50 |
| `PLATRIXA_ONBOARDING_VERIFICATION_TTL_SECONDS` / `_RESEND_COOLDOWN_SECONDS` / `_REVEAL_TTL_SECONDS` / `_APPLICATION_EXPIRY_DAYS` | timing |
| `PLATRIXA_ONBOARDING_MAX_PER_IP_HOUR` / `_MAX_PER_IP_DAY` / `_GLOBAL_DAILY_CAP` | abuse caps |
| `PLATRIXA_PUBLIC_ORIGIN` | verification/reveal links and API base URL in emails |
| `PLATRIXA_OWNER_NOTIFICATION_EMAIL` | owner recipient (absent → owner emails `SUPPRESSED`, dashboard still records them) |
| `PLATRIXA_EMAIL_PROVIDER` = `smtp` \| `none` | transport selection |
| `PLATRIXA_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_STARTTLS` / `_SSL`, `PLATRIXA_EMAIL_FROM` | SMTP transport |
| `PLATRIXA_OWNER_DASHBOARD_USER` / `_PASSWORD` | dashboard HTTP Basic auth (password unset ⇒ 503, fail closed) |
| `PLATRIXA_API_BASE_URL`, `PLATRIXA_MANAGEMENT_ASSERTION_SECRET`, `PLATRIXA_MANAGEMENT_ASSERTION_ISSUER`, `PLATRIXA_KEY_MANAGEMENT_TENANT_ID` | existing management plane, reused by the dashboard |

## Database

One idempotent file: `backend/database/platrixa_developer_onboarding_schema.sql`
(no `DROP`/`TRUNCATE`/`ALTER` of existing tables). Six new tables:
`platrixa_developer_access_requests`, `..._email_verifications`,
`..._key_deliveries`, `..._notifications`, `..._submission_handles` (opaque
`application_reference` lookup values) and `..._onboarding_audit`. The existing
`platrixa_api_keys` and `platrixa_tenant_quotas` are reused unchanged.

Apply with:

```bash
python -m backend.auth.init_metering      # existing quota table
python -m backend.auth.init_onboarding    # new onboarding tables (additive, re-runnable)
python -m backend.auth.init_onboarding --verify   # read-only compatibility check
```

The **runtime never executes DDL** (`PLATRIXA_ONBOARDING_AUTO_MIGRATE` is a
throwaway-store escape hatch only). If the schema is absent or older than the
build, onboarding answers `503 ONBOARDING_SCHEMA_NOT_READY` and creates
nothing. `--verify` reflects the live catalogue (no process cache) and exits
non-zero on a missing table or column, so it is safe as a deployment gate.

The application role needs `SELECT/INSERT/UPDATE/DELETE` on these tables and
nothing else: it owns no table and cannot `CREATE`. `init_onboarding` is the
only step that needs DDL rights.

Rollback: stop writing (unset `PLATRIXA_ONBOARDING_ENABLED`), then
`DROP TABLE` the six new tables in reverse dependency order (audit,
submission_handles, notifications, key_deliveries, email_verifications,
access_requests). Existing
keys, quota rows and tenants are untouched, so current developer access keeps
working. Revoke any keys issued during the trial window first.

## Notifications

Rows are created in the same transaction as the state change (dedupe key per
event), and **no SMTP work happens inside an HTTP request**: the endpoint only
enqueues. A durable dispatcher performs every send:

```bash
python -m backend.onboarding.dispatcher --interval 30   # long-lived worker
python -m backend.onboarding.dispatcher --once          # cron / one-shot
# or, from an orchestrator that already holds the token:
curl -X POST -H "x-platrixa-dispatch-token: $PLATRIXA_ONBOARDING_DISPATCH_TOKEN" \
     https://api.platrixa.com/v1/internal/onboarding/dispatch
```

Each pass selects the due ids and then claims **one row at a time,
immediately before its own send**, with a `claim_token`, `claimed_at` and
`lease_expires_at`. Claiming a whole batch up front was a real duplicate-send
defect: the tail rows sat `SENDING` while the head was delivered, their leases
expired, and a second worker re-sent them.

While a send is in flight the lease is **renewed** every `lease/3` seconds
(`_LeaseKeeper`), so a slow SMTP server cannot make a live send look
abandoned. Acknowledging is fenced by the claim token: a send whose claim was
stolen mid-flight is counted as `lost` and never writes `SENT`. A crashed
worker's claim expires and the row is recovered (`recovered`) automatically.
Retryable failures use bounded backoff, terminal ones stop. Every pass also
expires abandoned applications, so an idle intake still converges. Rows are
deduped per event; bodies are cleared on completion.

**Delivery guarantee.** At most one worker owns a row at a time, and only that
worker can acknowledge it. Delivery is nevertheless **at least once**: a
worker that dies after the provider accepted a message but before the
acknowledgement commits leaves the row `PENDING`, and it is retried. SMTP
cannot tell "accepted, then the sender lost the acknowledgement" from "never
accepted", so that window is unavoidable. It is minimised (renewal for the
whole send, acknowledgement immediately after) and made detectable — every
message carries a **stable Message-ID** derived from the notification id, so a
receiver that honours it recognises the duplicate instead of showing two
emails.

Supported operating model: any number of dispatchers, all on a
synchronised clock (NTP). Renewal happens at `lease/3`, so clock skew up to
about a third of the lease is tolerated; do not run dispatchers with
diverging clocks, and do not set a lease below one second.
Without a provider, rows stay `PENDING` with `EMAIL_NOT_CONFIGURED` and are
retried once a provider is set — the queue never pretends to have sent
anything.

SMTP AUTH requires STARTTLS or implicit TLS. With credentials configured and
both disabled, delivery is refused with `SMTP_INSECURE_AUTH` instead of
sending the password in clear; `PLATRIXA_SMTP_ALLOW_PLAINTEXT_AUTH=1` is a
dev/test-only escape hatch for a local relay.

### Client identity for caps

The backend never trusts caller-supplied `X-Forwarded-For`.

**Behind the Cloudflare Pages proxy (the production topology) no setting is
needed.** The proxy builds a fresh header set and Railway rewrites
`X-Forwarded-For` / `X-Real-IP` with the edge address, so the socket peer is
one shared address for every visitor. The `/v1` and `/api` Pages functions
therefore assert the real address:

```
x-platrixa-client-ip: <cf-connecting-ip>
x-platrixa-client-ip-signature: HMAC-SHA256(PLATRIXA_VISITOR_HMAC_SECRET,
    "V1|platrixa-client-ip|<address>|<visitor assertion signature>")
```

Binding the claim to that request's visitor assertion (verified and
nonce-consumed by the website middleware) means it cannot be forged by a direct
caller, cannot be replayed from another request, and disappears with the
assertion. A missing, malformed or unsigned claim is never trusted — the
request falls back to the next source. The verifier is
`backend/auth/client_ip_claim.py`; the golden vector in
`tests/fixtures/client_ip_claim_vector.json` is asserted by both the Python and
the JavaScript tests, so the two signers cannot drift.

The Next.js dev/preview proxy (`frontend/web/lib/platrixa-proxy.ts`) does not
send the claim: it runs on a developer machine where no trustworthy client
address exists, so local per-client caps use the transport peer. Only the
Cloudflare Pages functions have an address worth asserting.

On a topology WITHOUT that signed claim, configure exactly one of:

* `PLATRIXA_CLIENT_IP_HEADER` — an edge-written header. Only `cf-connecting-ip`,
  `x-real-ip`, `true-client-ip` and `fly-client-ip` are honoured.
* `PLATRIXA_TRUSTED_PROXY_COUNT` — number of proxies you operate; the client is
  read that many entries from the **right** of `X-Forwarded-For`, so
  attacker-chosen leftmost values are ignored.

With none of them available, per-client caps fall back to the socket peer. A
cap rejection is always an honest `429 RATE_LIMITED` with `Retry-After`;
nothing is accepted-and-dropped.

## Owner console (operator-only)

The owner dashboard is **not** part of the Cloudflare Pages artifact. Its API
route handler holds the owner credential and the management signing secret
server-side, so `scripts/build-pages.mjs` excludes the whole `app/owner`
subtree from the static export — otherwise the operator page itself would be
published as public HTML on the Pages host with no working backend behind it.

The console is a loopback-only server on the operator's machine, reusing the
normal Next build:

```bash
cd frontend/web
npm run build                        # full server build (owner API included)
npm run owner:start                  # next start -H 127.0.0.1 -p 3100
# then open http://127.0.0.1:3100/owner/onboarding
```

Required server-side environment (never in client code):
`PLATRIXA_OWNER_DASHBOARD_USER`, `PLATRIXA_OWNER_DASHBOARD_PASSWORD` (unset ⇒
every request is refused `503`, fail closed), `PLATRIXA_API_BASE_URL`,
`PLATRIXA_MANAGEMENT_ASSERTION_SECRET`, `PLATRIXA_MANAGEMENT_ASSERTION_ISSUER`
and `PLATRIXA_KEY_MANAGEMENT_TENANT_ID`.

Nothing is exposed publicly: the process binds loopback only, the browser
authenticates with HTTP Basic, and each request is signed as a fresh
management gateway assertion. Do not put this behind a public hostname without
Cloudflare Access (or equivalent) in front of it.

The token-gated `/operator/*` Pages function remains available for CLI use; it
never forwards the management token and signs every request itself.

## Email: SMTP setup guide

No provider credentials ship with the repository, so email is **unconfigured by
default**. Set these server-side (Railway → Variables, or your secret manager)
and never in client code or the website build:

```dotenv
PLATRIXA_EMAIL_PROVIDER=smtp
PLATRIXA_SMTP_HOST=smtp.postmarkapp.com      # or email-smtp.<region>.amazonaws.com, smtp.gmail.com, …
PLATRIXA_SMTP_PORT=587
PLATRIXA_SMTP_STARTTLS=1                     # 587 + STARTTLS (default)
PLATRIXA_SMTP_SSL=0                          # set 1 for implicit TLS on 465
PLATRIXA_SMTP_USERNAME=…
PLATRIXA_SMTP_PASSWORD=…                     # never logged, never returned by any route
PLATRIXA_EMAIL_FROM=no-reply@yourdomain.tld  # must be a verified sender at the provider
PLATRIXA_EMAIL_FROM_NAME=Platrixa
PLATRIXA_EMAIL_REPLY_TO=support@yourdomain.tld
PLATRIXA_PUBLIC_ORIGIN=https://yourdomain.tld
PLATRIXA_API_BASE_URL=https://api.yourdomain.tld/v1   # optional; defaults to $PUBLIC_ORIGIN/v1
PLATRIXA_OWNER_NOTIFICATION_EMAIL=ops@yourdomain.tld
PLATRIXA_KEY_DELIVERY_SECRET=<32+ random characters, rotate deliberately>
```

Verify configuration without sending anything:

```bash
python -m backend.auth.email_diagnostics            # what is set / missing (names only)
python -m backend.auth.email_diagnostics --probe    # real connect + AUTH, sends no message
echo $?                                             # 0 ok, 2 not configured, 3 probe failed
```

The probe reports `connected`, `authenticated`, `SMTP_AUTH_FAILED`,
`SMTP_TLS_FAILED` or `SMTP_UNAVAILABLE` and never includes credentials.

Provider notes: SES/Gmail/Postmark all work through the same transport. Gmail
requires an app password (not the account password) and the From address to
match the authenticated account. For implicit TLS use port 465 with
`PLATRIXA_SMTP_SSL=1` and `PLATRIXA_SMTP_STARTTLS=0`.

### Messages sent

| Event | Recipient | Kind |
|---|---|---|
| Application submitted | developer | `EMAIL_VERIFICATION` |
| Resend requested | developer | `EMAIL_VERIFICATION` (new token; the old one is consumed) |
| Provisioned | developer | `ONBOARDING` (activation, quota, one-time reveal link, curl example) |
| New application | owner | `NEW_ACCESS_REQUEST` |
| Needs review | owner | `REVIEW_REQUIRED` |
| Provisioning/delivery failure or missing configuration | owner | `PROVISIONING_FAILED` |
| Key rotation | developer | `ONBOARDING`-style delivery for the new key |

Delivery states: `PENDING → SENDING → SENT | FAILED | SUPPRESSED`. Retryable
failures back off (30s → 1h, 6 attempts); auth/refusal failures are terminal;
a missing provider records `EMAIL_NOT_CONFIGURED` and keeps the row due, so it
flushes immediately once SMTP is set. Bodies are cleared on `SENT`/`FAILED` so
live links do not linger. A successful SMTP hand-off is the only delivery claim
made; nothing reports "delivered to inbox".

## Developer quickstart (first API transaction)

```bash
export PLATRIXA_API_KEY=plx_live_…        # from the one-time link, server-side only

curl -X POST "https://api.yourdomain.tld/v1/process" \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  -d '{"raw_input": "purchased furniture from raj for rs.25000"}'
```

The response is the canonical envelope: `status` (`VERIFIED`, `REVIEW_REQUIRED`,
…), `engine_status`, `interpretation`, `accounting`, `reason_codes`. `VERIFIED`
is decided by the deterministic engine only — the model can never assert it.

```bash
curl "https://api.yourdomain.tld/v1/usage"       -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY"
curl "https://api.yourdomain.tld/v1/developer/jobs" -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY"
```

Authentication is the `X-Platrixa-API-Key` header (or `Authorization: Bearer`),
server-side only. Send the key from your backend; never from browser JavaScript,
a mobile app bundle, a URL or analytics. A `401` costs no quota; admitted
requests count against the monthly bucket shown by `/v1/usage`.

## Verification and key recovery

* **Verification link expired / already used** — open `/developer/verify` and
  use “Request a new verification link”, or `POST
  /v1/developer/resend-verification {"email": "…"}`. Resends are throttled (60s
  default, `429`), and the newest link invalidates older ones.
* **Key link expired** — `POST /v1/developer/keys/rotate` with the current key
  returns a new key id and emails a fresh one-time link. If the key itself is
  lost, the operator rotates it from the management plane
  (`/v1/owner/onboarding/tenants/{tenant}/keys/rotate`).
* **Key compromised** — rotate immediately; the previous key stops
  authenticating at the rotation commit. Revocation is also available in the
  management plane.
* **Status questions** — every submission response (new, duplicate or
  race-lost alike) returns the same opaque `application_reference`.
  `GET /v1/developer/access-status?reference=…&email=…` (both required) reports
  the state and whether review is pending, and never discloses an internal
  application or tenant id. `REVIEW_REQUIRED` means an operator must decide; no
  key is issued. `application_id` is accepted as a legacy alias for operators.

## Testing

```bash
# onboarding behaviour, security and isolation (SQLite store, 57 tests)
python -m pytest tests/test_developer_onboarding.py -q

# real SMTP: local aiosmtpd server, real login, real delivery, retries (8 tests)
python -m pytest tests/test_onboarding_email_smtp.py -q

# real PostgreSQL: migration, locks, concurrency, least privilege (14 tests)
python -m pytest tests/test_developer_onboarding_postgres.py -q

# end-to-end journey with the real deterministic kernel (1 test)
python -m pytest tests/test_onboarding_e2e_kernel.py -q

# release-audit regression suite: route policy, isolation, rate limiting,
# dispatcher claims, rotation, reference lookup, migration (54 tests)
python -m pytest tests/test_onboarding_remediation.py -q
```

The PostgreSQL suite starts a throwaway cluster in `/tmp` and applies the
repository's own `backend/database/*.sql` files, so the schema under test is the
one production uses. It never reads a production URL. Set
`PLATRIXA_SKIP_POSTGRES_TESTS=1` to skip it where `pgserver` is unavailable.

To rehearse against a server of the production major version instead (e.g. a
disposable PostgreSQL 18 cluster), point the same suite at it:

```bash
PLATRIXA_TEST_PG_URL=postgresql://user@127.0.0.1:55433/postgres \
  python -m pytest tests/test_developer_onboarding_postgres.py -q
```

Every test in that suite drops and re-creates only the onboarding/credential
tables, so it must never be aimed at a database that matters.

## Owner dashboard (deferred)

The `/owner/onboarding` UI and `/v1/owner/onboarding/*` APIs exist and are
covered by tests, but the dashboard is **out of scope for this iteration**: it
is not required for the developer journey and receives no further UI work here.
Owner authentication is unchanged and must not be weakened to simplify that UI.

## Deployment readiness checklist

1. `PLATRIXA_KEY_DELIVERY_SECRET` set (≥32 chars) — otherwise applications are
   verified but held, and the owner is notified.
2. SMTP variables + verified From address set; `python -m backend.auth.email_diagnostics --probe` exits `0`.
3. `PLATRIXA_PUBLIC_ORIGIN`, `PLATRIXA_API_BASE_URL`, `PLATRIXA_OWNER_NOTIFICATION_EMAIL` set.
4. `python -m backend.auth.init_onboarding` applied to the target database
   (additive, idempotent; existing keys/quota rows untouched), then
   `python -m backend.auth.init_onboarding --verify` exits `0` as the
   deployment gate. The application role needs DML only, never DDL.
5. Produce and deploy the **exact** `WEBSITE_ROUTE_SPECS` table: the backend
   (`backend/auth/surface_policy.py`) and both edge proxies
   (`frontend/web/lib/website-route-policy.js`,
   `frontend/functions/v1/[[path]].js`) must ship the same allowlist; the
   regression suite asserts they agree.
6. Client identity configured: `PLATRIXA_CLIENT_IP_HEADER` or
   `PLATRIXA_TRUSTED_PROXY_COUNT` matching the deployed proxy chain, so per-IP
   caps are real rather than one shared bucket.
7. Dispatcher running (`python -m backend.onboarding.dispatcher --interval 30`
   under a supervisor, or a cron `--once`), and
   `PLATRIXA_ONBOARDING_DISPATCH_TOKEN` set if an orchestrator calls the
   internal endpoint. `GET /v1/internal/onboarding/health` reports queue depth
   and last-pass outcome.
8. `GET /v1/ready` healthy; one controlled application submitted end to end;
   both the developer and owner messages received; `EMAIL_PROVIDER` still `smtp`.
9. Confirm `/developer`, `/developer/verify`, `/developer/key` render on the
   public site and the verification link opens that origin.
10. Keep the form behind the release switch until 5–9 pass.

## Limitations (stated, not hidden)

* **No provider credentials exist in this repository.** Delivery is proven
  against a real SMTP server on loopback (including AUTH); sending through a
  third-party provider still requires the account and the variables above.
* The owner dashboard requires the Node/server deployment (or the operator
  ingress). The static Cloudflare Pages build excludes its route handler.
* Admitted-request usage is per key, matching the existing metering gate; a
  second key for one tenant opens a second bucket.
* Database durability, locks and concurrency are verified on PostgreSQL 16.2
  (embedded) and rehearsed on PostgreSQL 18.7 (external cluster);
  production-specific tuning (pool size, statement timeouts) is not tuned here.
* Notifications and audit rows intentionally have no foreign key to
  applications: the audit trail must survive an operator deleting an
  application, and the audit writer is append-only.
