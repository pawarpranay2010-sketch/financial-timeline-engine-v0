# Platrixa — Production-Readiness Integration Audit (post-Phase 5H)

**Scope:** cross-component integration audit of the complete 25-step developer
journey across Phase 5A–5H. Read-only. No features, no kernel / Formula
Authority / Finance Knowledge Authority / grounding / public-API-contract
changes. The Phase 5H self-hosted llama.cpp transport work was not touched.

**Method:** full read of `api/` (main, rate_limit, results, status, schemas,
routes/{developer, async_api, observability, developer_keys, kernel,
_admission}) and `backend/auth/` (gate, idempotency, async_jobs, request_log,
api_keys); the two browser→backend proxies; the Next.js developer surface; and
`render.yaml` / `DEPLOYMENT.md`. Cross-component behaviour was exercised with
four temporary isolated reproductions (all deleted afterwards; no production
file was modified). The Phase 5A–5H regression and security suites were run
end to end.

---

## 1. Executive finding

**Verdict: the accounting kernel, Formula Authority, Finance Knowledge
Authority and grounding boundaries are production-grade and untouched by
anything found here. The developer platform around them is not: it is
component-tested, and the seams between the components are untested and
demonstrably broken.**

Three independent conclusions:

**(a) The component suites are genuinely strong, and they all pass.**
Phase 5A (52/52), 5B (50/50), 5C (59/59), 5D (54/54), 5E (52/52), 5F (26/26),
5G (44/44), 5H (45/45), hosted-API boundary (55/55), hosted-API security
(47/47), metered gate (52/52), and the security baseline (16/16 suites,
"BASELINE HOLDS"). Nothing I found challenges the deterministic core: no false
VERIFIED path, no weakened grounding, no authority bypass, no secret in a
response body, no traceback leakage. The webhook SSRF/DNS-rebinding hardening
is real and is covered by `fte_sec_03`. The engineering quality inside each
phase is high.

**(b) But the 25-step journey has never been run end to end, and the pieces
that would catch the defects below are outside the test net.** The two
browser→backend proxies are JavaScript and are exercised by no Python suite.
The deployment recipe (`render.yaml`) is exercised by no suite. The
cross-component assertions that matter most — *how many quota units does each
route consume*, *do the proxies forward the credential headers the gate
requires*, *does one request produce one observability row* — do not exist in
any suite. As a result the suite suite reports 100 % green while the deployed
developer path cannot authenticate at all.

**(c) Under the shipped deployment recipe, most of Phase 5C–5H is inert and
the developer API is open.** `render.yaml` sets `PYTHON_VERSION` and
`DATABASE_URL` only. Without `PLATRIXA_METERING_DATABASE_URL` the metering gate
never activates, and `_check_api_key` then fails *open* — so a blueprint deploy
serves an unauthenticated, unmetered `/v1/process`, while idempotency
(`400 IDEMPOTENCY_NOT_CONFIGURED`), async documents
(`400 ASYNC_NOT_CONFIGURED`), usage (`503 USAGE_NOT_CONFIGURED`), key
management (`400 API_KEY_MANAGEMENT_NOT_CONFIGURED`) and webhook registration
are all disabled. `render.yaml` says "See DEPLOYMENT.md for the full
environment-variable table"; that table documents none of those variables.

**What is genuinely production-ready:** the deterministic engine, the six-state
transport mapping, the Phase 5D result contract, the metered gate's atomicity,
the idempotency claim/replay/conflict semantics, the API-key lifecycle, the
tenant-scoped read paths, the error-disclosure discipline.

**What is merely component-tested:** every seam. Admission accounting, the
browser edge, the async worker lifecycle, the webhook delivery contract,
observability coverage, the 500/400/429 error envelope, and deployment
configuration.

---

## 2. Integration gaps

Severity: **CRITICAL** = ships broken / loses data or money / breaks the
security boundary. **HIGH** = breaks a documented workflow under realistic
load or configuration. **MEDIUM** = contract or accounting inconsistency.
**LOW** = dead code, duplication, cosmetic.

### C1 — CRITICAL — Document processing is completely unmetered

`api/routes/developer.py:206–216`: the shared `_metered_api_key_guard`
dependency calls `metered_gate.resolve_tenant` — authenticate **without
reserving** — for *every* endpoint it guards. Its own inline comment claims
"All other endpoints keep the original guard (authenticate + reserve)
unchanged", and its docstring claims "the reservation is ALREADY committed
before any processing begins". Both are false.

`authorize_request` / `reserve_unit` is called in exactly two places in the
whole repository (`grep` over `api/`, `backend/`, `platrixa/`):

- `api/routes/developer.py:825` — `POST /v1/process`
- `api/routes/async_api.py:714` — `POST /v1/documents`

`POST /v1/process/document` (developer.py:1039) reserves nothing.

**Measured (temporary reproduction, metering configured, gate stubbed):**

```
POST /v1/process (text)            http=200  quota_units_reserved=1
POST /v1/process/document          http=200  quota_units_reserved=0
POST /v1/documents (async)         http=202  quota_units_reserved=1
GET /v1/capabilities               http=200  quota_units_reserved=0
```

Consequence: the most expensive path in the product — OCR + document
understanding + model inference — is free, per key, per month. A single tenant
can issue unbounded metered-traffic document processing; `current_month_usage`
never moves, `GET /v1/usage` reports zero, and quota exhaustion (workflow 20)
can never be reached through the document route. No suite asserts
document-path quota accounting (`grep` across `scripts/` finds none).

### C2 — CRITICAL — The browser edge drops every credential the gate requires

There are two proxies, and neither forwards the developer-plane headers.

`frontend/functions/v1/[[path]].js` and `frontend/functions/api/[[path]].js`
both use the 5-entry allowlist
`["content-type","accept","authorization","accept-language","x-requested-with"]`.

**Measured (temporary reproduction, `fetch` stubbed, real proxy module):**

```
CLOUDFLARE PAGES /v1 FUNCTION — forwarded request headers:
{ "authorization": "Bearer zzz", "content-type": "application/json" }
  x-platrixa-api-key             forwarded=false
  x-platrixa-management-token    forwarded=false
  idempotency-key                forwarded=false
  x-request-id                   forwarded=false
```

`frontend/web/lib/platrixa-proxy.ts` (Next dev/preview) drops the same set and
additionally **overwrites** `x-platrixa-api-key` with a shared
`PLATRIXA_API_KEY`:

```
NEXT DEV/PREVIEW PROXY — forwarded request headers:
{ "content-type": "application/json",
  "x-platrixa-api-key": "operator-shared-key",
  "x-request-id": "req-123" }
  x-platrixa-management-token    forwarded=false
  idempotency-key                forwarded=false
  tenant key overwritten by PLATRIXA_API_KEY: true
```

Consequences:

- Through the Cloudflare Pages origin, no `/v1` request can ever carry a
  tenant key or a management token. On a metered deployment every call is
  `401`; on a zero-config deployment every call is **anonymous**.
- Workflows 4/5/6 (idempotent submit, replay, conflict) are impossible through
  the frontend: the header never arrives, so a retried submit is processed
  again and charged again.
- The `/developer` page advertises the API-keys, Usage and Requests console as
  "real endpoints". Those tabs cannot work on the deployed Pages origin
  (`403 API_KEY_MANAGEMENT_UNAUTHORIZED` / no credential at all).
- Silent wrong-tenant reporting: the Usage tab asks the user for their own API
  key, the proxy replaces it with the operator's shared key, and
  `GET /v1/usage` returns **the operator tenant's usage** with no error.
- The Next proxy also discards upstream response headers
  (`proxyResponse` emits only `content-type` and `cache-control`), so
  `Idempotent-Replayed` and `Retry-After` are invisible to the browser —
  workflow 5 cannot be observed through the frontend origin. (The Pages
  function does forward them.)

### C3 — CRITICAL — The async and idempotency stores leak a connection pool on every call

`backend/auth/gate.py:150–190` documents and fixes a real incident: the engine
cache "never hit" because the lookup used the raw URL while the entry was
stored under the normalized URL, so every call built a new Engine and
ConnectionPool; the 40-thread suite built **132 engines**, exhausted the
server's 100-connection cap, and surfaced as spurious fail-closed 503s. The fix
normalizes the URL **before** the cache lookup.

`backend/auth/idempotency.py:184–206` and `backend/auth/async_jobs.py:270–295`
contain the **same defect, unfixed**: both read the cache with the raw URL
(`_session_factory_cache.get(url)`) and store under the normalized URL
(`postgresql://` → `postgresql+psycopg2://`). The URL format documented in
`DEPLOYMENT.md` is exactly `postgresql://…`, so the cache never hits.

**Measured (temporary reproduction, `create_engine` stubbed, 5 successive
calls, `postgresql://` URL):**

```
idempotency  cache entries=1  engines built over 5 calls=5  cache_hit=5/5
async_jobs   cache entries=1  engines built over 5 calls=5  cache_hit=5/5
gate         cache entries=1  engines built over 5 calls=1  cache_hit=5/5
```

(5 engines built, 1 cache entry — every call misses.) Neither store has the
`_session_factory_lock` that `gate.py` added either, so N concurrent
first-callers each build an engine and each re-run the table DDL.

Consequence: every idempotent `/v1/process` and **every async worker poll**
(0.5 s cadence) creates a new connection pool that is never disposed. This
reproduces the exact production failure the gate fix was written for —
connection exhaustion surfacing as `503 IDEMPOTENCY_UNAVAILABLE` /
`503 ASYNC_UNAVAILABLE`. It fails closed (no auth or quota bypass), but it is a
sustained-availability failure that grows monotonically with traffic.

### H1 — HIGH — `render.yaml` provisions none of the Phase 16 / 5C–5H environment

`render.yaml` `envVars` = `PYTHON_VERSION`, `DATABASE_URL`. Missing:
`PLATRIXA_METERING_DATABASE_URL`, `PLATRIXA_DEV_API_KEY`,
`PLATRIXA_KEY_MANAGEMENT_TOKEN`, `PLATRIXA_KEY_MANAGEMENT_TENANT_ID`,
`PLATRIXA_WEBHOOK_SIGNING_KEY`, `PLATRIXA_PUBLIC_BASE_URL`,
`PLATRIXA_REQUEST_LOG_RETENTION_DAYS`, `PLATRIXA_RULE_PACK_PATH`,
`PLATRIXA_TRUSTED_PROXY_COUNT`.

Everything in §1(c) follows directly. Additionally `/v1/ready` reports
`status: "ready"` in this configuration while the entire admission, replay,
async, webhook and observability stack is switched off — readiness does not
reflect the state an operator actually needs to know.

### H2 — HIGH — Async worker has no lease renewal; jobs can be processed twice

`async_jobs.JOB_LEASE_SECONDS = 120` (async_jobs.py:57).
`claim_next_job` re-claims any row that is `PROCESSING` with an expired lease
(async_jobs.py:469). Nothing ever extends a lease: `lease_expires_at` is only
written by `claim_next_job`, `complete_job`, `fail_job` and `_release_lease`.

A document that takes longer than 120 s is therefore reclaimed **while it is
still running**. `DEPLOYMENT.md` itself records a ZeroGPU cold start "observed
up to ~90 s", before OCR and document understanding. Result: two concurrent
processors on one job, model cost twice, quota charged once, and two webhook
deliveries (both carrying the same deterministic event id, so only an
id-deduplicating consumer notices). `complete_job` matches
`WHERE status='PROCESSING'`, so both writes succeed.

### H3 — HIGH — The worker only starts on submission; stranded jobs after restart

`ensure_worker_started()` is called from exactly one place —
`create_document_job` (async_api.py:723). After a restart, crash, or scale-out,
`QUEUED` and expired-lease jobs are never picked up until a *new* submission
arrives. A job that was `PROCESSING` when the process died stays `PROCESSING`
until some unrelated user submits a new document. There is no startup
recovery scan, and `/v1/ready` does not check for stranded work.

### H4 — HIGH — The webhook contract promises two events it can never deliver

- `EVENT_BY_API_STATUS` (async_jobs.py:77) has **no `PROCESSING` key**.
  `_fail_job(..., retryable=True)` computes
  `api_status = public_status_for_error_code("PROVIDER_UNAVAILABLE") ==
  "PROCESSING"` and calls `_emit_webhooks(record, "PROCESSING")`, which returns
  immediately (`event is None`). **A job that ends `FAILED` with
  `retryable=true` — the provider-unavailable case — sends no webhook at all**,
  even though `document.failed` is in the registration enum and the poll
  endpoint reports the failure.
- `EVENT_PROCESSING = "document.processing"` is in `ALL_EVENTS` and in the
  OpenAPI enum at `POST /v1/webhook-endpoints`, but nothing ever maps to it.
  An endpoint registered for it can never fire.

Suite 81 (52/52) asserts only the `VERIFIED → document.completed` mapping.

### H5 — HIGH — The Phase 15 single-key gate short-circuits Phase 16 / 5G tenant keys

`_metered_api_key_guard` runs `_check_api_key(request)` **first**
(developer.py:200). If `PLATRIXA_DEV_API_KEY` is set, only that exact value
passes; a key created through `POST /v1/developer/api-keys` is rejected `401`
before `resolve_tenant` is ever called. A deployment that sets both variables
locks out every tenant key it just created through the management plane. (This
repository's own `.env` sets `PLATRIXA_DEV_API_KEY`; the failure was hit
first-hand while reproducing M1.) Conversely, with neither variable set,
`_check_api_key` fails **open** while `api/routes/_admission.py` fails
**closed** for `/api/v1` — two opposite policies for the same question, in the
same process.

### H6 — HIGH — The main product UI bypasses every Phase 15/16–5H control

`frontend/web/lib/api.ts:70` posts `POST /api/v1/kernel/process` — the route
`_admission.py:24–27` deliberately keeps public. That route is un-authenticated,
un-metered, writes no request-log row, supports no idempotency, and is capped
at 30 req/min per bucket. It also returns a **different, older contract**
(`KernelProcessResponse`: no `api_status`, no `reason_codes`, no `evidence`, no
`lineage`, no `rule_evidence`, no `metadata`, no `retryable`) and — unlike
`/v1/process`, which "makes NO persistence calls" — it writes to Postgres via
`PostgresResultPersistence` (kernel.py:99, 215).

So one engine, two live processing contracts, and the primary browser surface
is the one that persists raw financial input and bypasses every gate.

### M1 — MEDIUM — The `/v1` error envelope is inconsistent in three places

- **500** → `{"detail": "internal error"}` (main.py:76–79). No `api_version`,
  no `error.code`, no `api_status`, no `retryable`, no `request_id`.
  `api/status.py` documents 500 as mapping to the public state `FAILED`; it
  does not.
- **400 `REQUEST_MALFORMED`** → `developer_validation_handler` never sets
  `request_id`, while the 401 path on the same route does.
- **429 `RATE_LIMITED`** (main.py:118–131) is a hand-built envelope containing
  only `{code, message}` — no `api_status` / `api_status_label` / `retryable` /
  `request_id`; and `RATE_LIMITED` is absent from `STATUS_BY_ERROR_CODE`, so if
  it were mapped it would fail closed to `FAILED` / `retryable=false` on a 429.
- **Auth status differs by plane**: `401` on the data plane (`/v1/usage`, the
  metered gate) vs `403` on the management plane (`developer_keys._authorize`,
  `observability._management_tenant`) for the same class of failure.
- **Document route 415/413** — `_read_multipart_form` raises bare
  `HTTPException(413, "document too large")` and `(400, "malformed multipart
  body")`, bypassing `_error_response`; the scoped `gate_error_handler` only
  rewrites codes in `_GATE_ERROR_MESSAGES`, so these return `{"detail": …}`.

**Measured (temporary reproduction):**

```
--- 400 malformed body, with a well-formed X-Request-Id ---
http 400  { "error": { "code": "REQUEST_MALFORMED", ..., "retryable": false } }
carries request_id: False
--- 401 on the same route, same header ---
http 401  { "error": { "code": "UNAUTHORIZED", ..., "request_id": "corr-abc-123" } }
--- 500 unhandled exception ---
http 500  { "detail": "internal error" }
```

### M2 — MEDIUM — Quota exhaustion is published as `INVALID_INPUT`, not retryable

**Measured directly from `api/status.py`:**

```
QUOTA_EXHAUSTED     -> INVALID_INPUT  retryable=False  label="Invalid input"
UNAUTHORIZED        -> INVALID_INPUT  retryable=False  label="Invalid input"
PROVIDER_UNAVAILABLE-> PROCESSING     retryable=True
```

`INVALID_INPUT` is documented in `api/status.py` as "malformed transport request
or input rejected by the public input contract" — a monthly quota is neither.
`api/results.py::next_action_for("INVALID_INPUT")` returns "fix the request;
retrying unchanged will fail again", which is exactly wrong advice for a quota
that resets at the month boundary. The developer's error table lists
`QUOTA_EXHAUSTED 429` with no state mapping, so the contradiction is invisible
to a reader. `UNAUTHORIZED → "Invalid input"` is the same class of error.

### M3 — MEDIUM — Observability covers exactly one route

`_record_request_metadata` has a single caller
(`_process_v1_idempotent`, developer.py:829) and hardcodes
`endpoint="/v1/process"`. So `/v1/process/document`, `POST /v1/documents`,
`POST /v1/webhook-endpoints` and `/v1/capabilities` never appear in request
history, and neither do 500s.

The concrete cross-component break: `POST /v1/documents` accepts the client's
`X-Request-Id` and stores it as the job's `request_id`; `GET /v1/jobs/{id}`
returns it as `request_id`. `GET /v1/developer/requests/{that id}` then returns
`404 REQUEST_NOT_FOUND` — for **every** async request. Workflows 11 and 16 do
not compose.

`capability_id` is dead: the only caller never passes it, so the column, the
index field and the detail field are always `null` although all three are
advertised.

### M4 — MEDIUM — "Per-key usage" is not per key, and the two aggregates disagree

`get_key_usage` (`/v1/developer/api-keys/{key_id}/usage`) returns the **tenant**
aggregate and sums **all** rows for the tenant (no `is_active` filter).
`get_tenant_usage` (`/v1/developer/usage`) sums **active rows only**
(`active = [r for r in rows if r.is_active]`). After a revoke, the two
"tenant" numbers for the same month disagree on both `monthly_limit` and `used`.
The endpoint admits this in a `note` field. The frontend exports
`fetchManagedKeyUsage` but never calls it, so workflow 17 is not exposed in the
UI at all, and neither usage endpoint appears in the endpoint list on
`/developer`.

### M5 — MEDIUM — Raw financial input is persisted indefinitely in the async store

`async_jobs.create_job` writes `request_json` containing the full base64
document or `raw_input` (async_api.py:723–727). There is no retention, TTL or
prune anywhere in `async_jobs.py`. Compare: idempotency snapshots 72 h
(`IDEMPOTENCY_RETENTION_HOURS`), request metadata 30 days
(`REQUEST_LOG_RETENTION_DAYS`). `platrixa_async_jobs` is therefore a permanent
archive of every submitted financial document, with no documented retention
policy and no deletion path.

### M6 — MEDIUM — 16 characters of the live API key are stored and served

`developer.py:830`:
`admitted_ctx = (adm_ctx, (provided or "")[:16] or None)` — the first 16
characters of the **raw** key are passed as `key_prefix` into
`request_log.record_request`, which persists them and returns them from
`GET /v1/developer/requests` and `GET /v1/developer/requests/{id}`.

`backend/auth/request_log.py:10–12` states: "No raw keys, no key hashes, no
secrets are stored or returned. Only the *masked* `key_prefix` is recorded."
The value stored is not the masked 5G `key_prefix` (which the same UI shows
from `/v1/developer/api-keys`) — it is a live, unmasked prefix of the
credential. `developer.py`'s own comment ("Metadata only — no raw input, no key
material") is also contradicted.

### L1 — LOW — Three copies of the `/v1` error envelope

`developer._error_envelope` (canonical),
`observability._error_envelope` (`retryable = api_status == "PROCESSING"`) and
`developer_keys._error_envelope` (a hard-coded 6-entry literal dict instead of
`RETRYABLE_BY_PUBLIC_STATUS`). Equivalent today, three places to drift.
`async_api.py` correctly imports the canonical one.

### L2 — LOW — Dead code

`api/results.py::next_action_for` and `::unavailable` — never called by any
route. `api/status.py::is_success_status`, `::is_retryable_status`,
`_SUCCESS_BY_PUBLIC_STATUS` — never used outside the module.
`request_log.capability_id` — never written. `developer_keys` declares
`x_platrixa_management_token: Optional[str] = Header(default=None)` on all four
routes and never reads it (authorization reads `request.headers` directly).
`developer._read_multipart_form` ends in `try: … finally: pass`.
`developer.validation_handler` wraps `original_handler` in
`register_developer_error_handlers` with no change.

### L3 — LOW — Two different `X-Request-Id` sanitizers

`developer._sanitize_request_id` uses `^[A-Za-z0-9._-]{1,128}$`;
`observability._sanitize_request_id` uses `str.isalnum()` (which accepts
non-ASCII letters) plus the same 128 cap. They disagree on which ids are valid,
and `GET /v1/developer/requests/{request_id}` uses the looser one.

### L4 — LOW — Mispaired status vocabulary in the job poll

For a completed `UNSUPPORTED_TRANSACTION` job the body is
`status: "UNSUPPORTED_TRANSACTION"` (engine vocabulary) with
`status_label: "Unsupported"` (public vocabulary). `DeveloperJobStatusResponse`
declares `status: str`, so the mismatch is unflagged.

### L5 — LOW — `POST /v1/documents` and key creation are not idempotent

Retrying a webhook registration after a timeout creates a **second live
endpoint** that receives duplicate events forever; there is no `GET` or
`DELETE` for webhook endpoints, so an endpoint cannot be removed and a lost
secret (returned exactly once) cannot be rotated in place. Key creation has the
same retry hazard.

---

## 3. Broken workflows

Ordered by the 25-step journey. Steps not listed work as documented.

**Steps 1–2 — obtain a key and authenticate, through the frontend origin.**
Broken. `GET /v1/usage` with a valid tenant key through the Pages origin sends
no key header (C2, measured) → `401 UNAUTHORIZED` on a metered deployment, or
an anonymous success on a zero-config one. Through the Next origin the key is
replaced by `PLATRIXA_API_KEY` → the wrong tenant's usage, no error.

**Steps 4–6 — idempotent submit / replay / conflict, through the frontend
origin.** Broken. `idempotency-key` is dropped by both proxies (C2, measured), so
the second submit is processed and charged again; the client can never observe
`Idempotent-Replayed: true` through the Next proxy because upstream response
headers are discarded. Server-to-server callers (no proxy) are correct — suite
79 passes 59/59 — so this is invisible to every existing test.

**Steps 10–14 — async submit, poll, result, webhook, authenticity.** Partially
broken.
- On the shipped `render.yaml` deploy, step 10 returns
  `400 ASYNC_NOT_CONFIGURED` (H1) — the feature simply does not exist.
- With a store configured, a job that fails on provider unavailability emits
  **no webhook** (H4), although the poll endpoint reports the failure and
  `document.failed` is subscribed.
- A webhook registered for `document.processing` can never fire (H4).
- Steps 11–12 themselves are correct and tenant-scoped.

**Step 16 — request history.** Broken for async. Take the `request_id` returned
by `POST /v1/documents` / `GET /v1/jobs/{id}`, then
`GET /v1/developer/requests/{request_id}` → `404 REQUEST_NOT_FOUND` (M3).
Document-path and webhook-registration requests are likewise absent.

**Step 17 — API-key usage.** Not implemented per key. The endpoint returns a
tenant aggregate; the two aggregates can disagree after a revoke (M4); the
frontend does not call it and does not list it (M4).

**Step 20 — quota exhaustion.** Reachable on `/v1/process` and
`/v1/documents`, but the response is published as
`api_status: "INVALID_INPUT"`, `retryable: false` (M2), and unreachable
entirely through the document route (C1).

**Step 21 — rate limiting.** The 429 body has no `api_status`,
`api_status_label`, `retryable` or `request_id` (M1). Behind Cloudflare Pages
(or any proxy) with `PLATRIXA_TRUSTED_PROXY_COUNT` unset — the default — the
bucket key is the transport peer, so the whole internet shares one 30 req/min
`/api/v1` bucket and the product UI throttles globally. Documented as a known
limitation in `reports/SECURITY_BASELINE.md:398`, not in `DEPLOYMENT.md`.

**Step 22 — provider/model unavailability.** Sync is correct (`503`,
`retryable: true`). Async loses the notification (H4) and the job is not
re-queued even though `retryable=true` — there is no retry queue, so "retryable"
is a hint the platform does not honour for a job already in the FAILED state.

**Step 23 — malformed input.** Works, but the response drops the caller's
`X-Request-Id` (M1, measured) and therefore cannot be correlated with the
server log.

**Step 24 — database/store failure.** Metering and idempotency fail closed with
correct envelopes. But once C3 exhausts connections, the same fail-closed 503s
become permanent; and any unhandled exception returns a non-enveloped 500 (M1).

**Step 25 — tenant isolation.** Holds in the backend: jobs, results, webhooks,
request history and request detail are all tenant-scoped server-side, the
idempotency primary key is `(tenant_id, key_hash)` (no cross-tenant replay),
and the management plane maps cross-tenant references to an indistinguishable
404. The break is at the edge: the Next proxy substitutes the operator's
credential, so a browser caller is shown the operator tenant's usage with no
error (C2). That is a wrong-tenant disclosure caused by the proxy, not by the
API.

---

## 4. Security gaps

Only findings I can demonstrate or read directly.

**S1 — HIGH — The shipped deployment serves an unauthenticated, unmetered
`/v1/process`.** `render.yaml` sets no `PLATRIXA_DEV_API_KEY` and no
`PLATRIXA_METERING_DATABASE_URL`; `_check_api_key` returns `None` when no key is
configured, and `api/routes/_admission.py` refuses to extend that fail-open to
`/api/v1`. This is documented zero-config behaviour, not a code regression — but
the deployment recipe produces it silently, and the same deploy also disables
every Phase 5C–5H control. C1 means a tenant key does not help on the document
route even once metering is on.

**S2 — MEDIUM — Live API-key material is persisted and served.**
`developer.py:830` stores the first 16 characters of the raw key and
`request_log` returns it through the management plane (M6), against its own
stated privacy boundary.

**S3 — MEDIUM — Unauthenticated write access to the customer's database.**
`POST /api/v1/kernel/process` is deliberately public, is rate-limited only at
30 req/min per bucket, and persists the submitted financial input via
`PostgresResultPersistence`. Anyone who learns the host can insert rows into
the application database. The developer route explicitly does not persist, so
the persistence path is reachable only through the unauthenticated one.

**S4 — No finding — checked and clean.** No secret appears in any response body
or log line; error bodies carry codes, not tracebacks or paths. The global 500
handler returns a fixed string. `_constant_time_equal` is length-independent
(both operands SHA-256'd before `compare_digest`). Webhook secrets are stored
sealed, never echoed, and returned exactly once. Webhook targets are validated
at registration **and** re-resolved at delivery with a globally-routable check
plus `allow_redirects=False` (suite `fte_sec_03` passes). Rule packs can only
come from `PLATRIXA_RULE_PACK_PATH`; the request schema exposes no code hook.
The global 500 handler is not a client-key oracle.

---

## 5. Reliability gaps

**R1 (CRITICAL) —** C3: unbounded engine/pool creation in the idempotency and
async stores, reproducing the incident `gate.py` documents having already fixed.

**R2 (HIGH) —** H2: no lease renewal → duplicate processing and duplicate
webhook delivery for jobs longer than 120 s.

**R3 (HIGH) —** H3: no startup recovery scan → stranded `QUEUED` and
`PROCESSING` jobs until an unrelated submission.

**R4 (HIGH) —** H4: webhook delivery misses the retryable-failure case and can
never emit `document.processing`. Single-attempt, no queue and no delivery
record are documented honestly, but the two cases above are not — they are
presented as part of the working contract.

**R5 (MEDIUM) —** The rate limiter is per-process and in-memory. On a single
Render instance that is acceptable; at two instances the effective limit is
120 req/min for `/api/v1` and no global control exists. The `/v1` surface has
tenant quota behind it, but the public `/api/v1` surface does not.

**R6 (MEDIUM) —** `request_log._ensure_worker` race:
`with (_worker_lock or threading.Lock())` — on the first call `_worker_lock` is
`None`, so a throwaway lock is used. Two concurrent first-callers can both
create a queue and both start a writer thread; the second assignment to the
module-global `_queue` orphans the first queue, and any rows already enqueued
into it are silently lost (the append is best-effort by design, so the loss is
invisible).

**R7 (MEDIUM) —** Two connection pools on the same database per process: the
gate/request-log engine and the async-jobs engine, both with SQLAlchemy defaults
(5 idle + 10 overflow) and no sizing from the platform's connection budget.

**R8 (LOW) —** `async_jobs._session_factory` and `idempotency._session_factory`
run their DDL on every call (a consequence of R1), so the DDL is on the hot
path of every poll.

---

## 6. Documentation gaps

Only where implementation and documentation disagree.

**D1 —** `render.yaml`: "See DEPLOYMENT.md for the full environment-variable
table." `DEPLOYMENT.md`'s table documents none of
`PLATRIXA_METERING_DATABASE_URL`, `PLATRIXA_DEV_API_KEY`,
`PLATRIXA_KEY_MANAGEMENT_TOKEN`, `PLATRIXA_KEY_MANAGEMENT_TENANT_ID`,
`PLATRIXA_WEBHOOK_SIGNING_KEY`, `PLATRIXA_REQUEST_LOG_RETENTION_DAYS`,
`PLATRIXA_RULE_PACK_PATH`, `PLATRIXA_TRUSTED_PROXY_COUNT`.
`docs/HOSTED_API.md` covers most of them; the deployment document an operator is
pointed at does not. `PLATRIXA_PUBLIC_BASE_URL` is documented **nowhere** in the
repository — and when unset, `_job_urls` returns relative
`/v1/jobs/{id}` and `/v1/results/{id}` in the 202 response, which a
server-to-server client cannot follow.

**D2 —** `api/routes/developer.py:_metered_api_key_guard` docstring: "the
reservation is ALREADY committed before any processing begins" — false for
every endpoint except `POST /v1/process`. Inline comment: "All other endpoints
keep the original guard (authenticate + reserve) unchanged" — false (C1).

**D3 —** `api/schemas.py:443` (DeveloperWebhookEndpointResponse): the secret "is
never stored in plaintext (SHA-256 hash only) and never shown again." The
implementation is a **reversible** SHA-256-CTR seal
(`seal_webhook_secret`), because the worker must recover the plaintext to sign.
`async_api.py` and `async_jobs.py` both describe the seal correctly and
`async_jobs.py` is explicit that the design is
"authenticated-confidentiality-*partial*". The schema docstring is the wrong
one, and it is the wrong one about a security property.

**D4 —** `api/routes/developer_keys.py` module docstring: revocation is
"durable, idempotent". A repeat revoke returns `404 API_KEY_REVOKED`, not the
original 200.

**D5 —** `api/status.py` module docstring: `FAILED` covers "an unexpected
server-side failure (500)". The 500 carries no `api_status` at all (M1).

**D6 —** `backend/auth/request_log.py:10–12`: "No raw keys … are stored or
returned. Only the masked `key_prefix` is recorded." False (M6).

**D7 —** `frontend/web/app/developer` + `developer-section.tsx` advertise the
API-keys / Usage / Requests console as "real endpoints" and
`developer-api.ts` says it "mirroring the real backend routes". The mirror is
accurate at the route level; the deployed origin cannot authenticate to any of
them (C2). The frontend comment in `platrixa-proxy.ts` — "Mirrors the repository
's existing Cloudflare Pages proxy pattern" — is also inaccurate in the other
direction: the two allowlists differ (only the Next one forwards
`x-request-id`).

**D8 —** `developer-section.tsx`: "Request history … A request's full 5D result
envelope is returned only while its 72-hour idempotency snapshot exists." True,
but the snapshot is only ever written for `/v1/process`, so for every other
endpoint the detail view always says `not_retained` without saying why (M3).

---

## 7. Recommended next phase

**Phase 5I — Admission & Edge Alignment (one phase, one boundary).**

This is the single highest-value next step because it is the only gap that
blocks *every* downstream workflow at once. Today a request cannot reach a
tenant-scoped component through the shipped edge (C2), and a request that does
reach it is not charged (C1) and can exhaust the database (C3). Fixing
anything else first — more capability work, more UI, more model work — would
be building on a boundary that does not yet work.

Scope, in priority order:

1. **One credential-forwarding contract for both proxies.** A single exported
   allowlist including `x-platrixa-api-key`, `x-platrixa-management-token`,
   `idempotency-key`, `x-request-id`, and explicit pass-through of
   `Idempotent-Replayed` / `Retry-After` / `Retry-After` on the way back. Make
   the Next proxy refuse to *overwrite* a caller-supplied key rather than
   silently substituting the operator's. Then test the **real** proxy modules
   against a **real** app instance — a JS test, not a code review.
2. **One reservation choke point.** Move the unit reservation out of the two
   per-route call sites into a single admission step that runs after
   authentication and after the idempotency claim, so every admitted request
   reserves exactly one unit. Add a table-driven test asserting
   `units_reserved == 1` for every admitted route and `0` for every
   unauthenticated one — the assertion that does not exist today.
3. **Fix C3 in both stores** with the same fix `gate.py` already uses
   (normalize before lookup, add the lock), and assert the cache hits in a test.
4. **Decide the Phase 15 ∥ Phase 16 precedence explicitly** — either the
   single key is an additional accepted credential, or it is documented as
   exclusive — and delete the two false docstrings while doing it.
5. **Make `/v1/ready` report the admission stack** (metering, idempotency,
   async, webhook signing) rather than only the provider, and add the missing
   variables to `render.yaml` + `DEPLOYMENT.md` with `PLATRIXA_PUBLIC_BASE_URL`
   documented alongside them.

Items 1–3 are the ones that change observable production behaviour. Items 4–5
are the cheap ones that stop the next operator from shipping the blueprint as-is.

Everything in §2 marked HIGH/MEDIUM that is *not* in that list (H2 lease
renewal, H3 startup recovery, H4 webhook contract, M1 error envelope, M2 status
mapping, M3–M6 observability/retention/key-prefix) should be scheduled after
5I, not before it.

---

## 8. Test evidence

Every test executed in this audit, with its real result. Nothing below is
inferred or reconstructed.

### Regression suites (run detached, `python3 scripts/<suite>.py`)

| Suite | Result |
|---|---|
| `fte_fyjc_77_six_state_api_status_test` (Phase 5A) | exit 0 — **52/52 PASS** |
| `fte_fyjc_78_phase5b_capability_discovery_test` (Phase 5B) | exit 0 — **50/50 PASS** |
| `fte_fyjc_79_phase5c_idempotency_test` (Phase 5C) | exit 0 — **59/59 checks passed — ALL PASS** |
| `fte_fyjc_80_phase5d_result_contract_test` (Phase 5D) | exit 0 — **54/54 PASS** |
| `fte_fyjc_81_phase5e_async_documents_test` (Phase 5E) | exit 0 — **52/52 PASS** |
| `fte_fyjc_82_phase5f_openapi_contract_test` (Phase 5F) | exit 0 — **26/26 PASS** |
| `fte_fyjc_83_phase5g_api_key_lifecycle_test` (Phase 5G) | exit 0 — **44/44 checks passed** (also asserts suite 79 passes unchanged) |
| `fte_fyjc_84_phase5h_observability_test` (Phase 5H) | exit 0 — **45/45 checks passed** (also asserts suites 79 + 83 pass unchanged) |
| `fte_fyjc_62_hosted_api_boundary_test` | exit 0 — **55/55 checks passed** |
| `fte_fyjc_65_hosted_api_security_test` | exit 0 — **47/47 checks passed** |
| `fte_fyjc_66_metered_gate_test` | exit 0 — **52/52 PASS** |

### Security baseline

`security_baseline_test.py` — exit 0, **16/16 suites passed**,
"BASELINE HOLDS — every required security property still passes."

```
api_contract     6 pass / 0 fail      financial_truth  4 pass / 0 fail
invariant        1 pass / 0 fail      security         5 pass / 0 fail
  fte_sec_01_grounding_fail_closed_test   26 markers  0.32s
  fte_sec_02_api_authz_admission_test     21 markers 35.45s
  fte_sec_03_webhook_ssrf_seal_test       26 markers  0.75s
  fte_sec_04_error_disclosure_test        18 markers  1.09s
  fte_sec_05_dependency_policy_test       22 markers  3.73s
  _sec_invariant_probe                    16 markers 35.86s
  fte_fyjc_grounding_verifier_test        18 markers  0.17s
  fte_fyjc_53_grounding_verification_wiring_test 77 markers 0.47s
  fte_fyjc_52_kernel_boundary_test       130 markers  2.15s
  fte_invoice_false_verified_gate_test    53 markers  0.31s
  fte_fyjc_59_status_contract_test        22 markers  0.61s
  fte_fyjc_80_phase5d_result_contract_test 55 markers 4.41s
  fte_fyjc_81_phase5e_async_documents_test 53 markers 8.41s
  fte_fyjc_66_metered_gate_test           56 markers  5.08s
  fte_fyjc_83_phase5g_api_key_lifecycle_test 46 markers 11.25s
  fte_fyjc_84_phase5h_observability_test  49 markers 16.87s
```

### Temporary isolated reproductions (all deleted afterwards)

No production file was modified. Each script was created under `scripts/` with
a `tmp_audit_` prefix, executed, and removed; `git status` confirms no trace
remains.

1. **`scripts/tmp_audit_repro_quota.py`** — FastAPI app with the real
   `developer` + `async_api` routers, metering gate stubbed, document
   processor stubbed. Result: `POST /v1/process` = 1 unit,
   `POST /v1/process/document` = **0**, `POST /v1/documents` = 1,
   `GET /v1/capabilities` = 0. → **C1**.
2. **`scripts/tmp_audit_repro_error_contract.py`** — real routers, real
   `register_developer_error_handlers`, a client that raises, and a copy of
   `api/main.py`'s 500 handler. Result: 400 `REQUEST_MALFORMED` without
   `request_id` while 401 carries it; 500 returns `{"detail": "internal error"}`
   with no envelope. → **M1**.
3. **`scripts/tmp_audit_repro_proxy_headers.mjs`** — imports the real
   `frontend/functions/v1/[[path]].js` with `fetch` stubbed. Result: only
   `content-type` + `authorization` forwarded; API key, management token,
   `idempotency-key`, `x-request-id` all dropped. → **C2**.
4. **`scripts/tmp_audit_repro_next_proxy_headers.mjs`** — imports the real
   `frontend/web/lib/platrixa-proxy.ts` (Node type-stripping) with `fetch`
   stubbed. Result: management token and `idempotency-key` dropped;
   `x-platrixa-api-key` replaced by `PLATRIXA_API_KEY`. → **C2**.
5. **`scripts/tmp_audit_repro_engine_cache.py`** — `create_engine` stubbed;
   5 successive `_session_factory()` calls per store with a
   `postgresql://` URL. Result: `idempotency` 5 engines / 1 cache entry,
   `async_jobs` 5 engines / 1 cache entry, `gate` (the fixed one) 1 engine /
   1 cache entry. → **C3**.

### Direct measurements from the real modules (no stubbing)

- `public_status_for_error_code` over the transport codes →
  `QUOTA_EXHAUSTED → INVALID_INPUT, retryable=False, label "Invalid input"`.
  → **M2**.
- `grep` for `authorize_request` / `resolve_tenant` across `api/`, `backend/`,
  `platrixa/` → reservations exist only at `developer.py:825` and
  `async_api.py:714`. → **C1**.
- `grep` for `EVENT_BY_API_STATUS` → no `PROCESSING` key; suite 81 asserts only
  the `VERIFIED` mapping. → **H4**.
- `grep` for `lease_expires_at` → written only by claim / complete / fail /
  release; no renewal. → **H2**.

### Environment note

The suites were run after clearing stale `/tmp/platrixa_*_pgdata` directories
(left over from earlier runs, which make `security_baseline_test.py` hang
indefinitely). Python 3.10, FastAPI + Starlette `TestClient`, 2 vCPU sandbox.
