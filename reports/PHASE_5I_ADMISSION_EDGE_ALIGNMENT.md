# Phase 5I — Admission & Edge Alignment

Implements the recommended next phase from
`reports/PRODUCTION_READINESS_INTEGRATION_AUDIT.md` (finding: the
deterministic core is production-grade, the platform seams are not).

**Scope boundary respected.** No change to the local llama.cpp transport or
GGUF artifact work, Modal, HF ZeroGPU, 5J (bulk), 5K (queue/worker redesign),
new financial capabilities, kernel logic, Formula Authority, Finance Knowledge
Authority, grounding rules, or the six-state processing contract. Verified:
`git diff --stat -- backend/kernel backend/rules backend/grounding backend/maths
core formula_engine platrixa` is **empty** — the
`MODEL → CandidateSemanticIR → Schema → Grounding → Deterministic Authority`
chain is byte-identical.

**Production status: NOT_READY.** The boundary is now coherent and covered by
anti-vacuous tests, but §11 lists what remains unproven or unfixed, including
the async worker lease/startup defects that are explicitly out of scope here.

---

## 1. Changes

| File | Change |
|---|---|
| `backend/auth/admission.py` | **NEW** — the single authoritative admission boundary: `admit()` (authenticate + reserve exactly one unit), `authenticate_only()` (read planes), deterministic Phase 15/16 precedence, `AdmissionContext`, `readiness_block()`. The only constant-time comparison implementation. |
| `api/routes/developer.py` | Guard (`_metered_api_key_guard`) now authenticates **only**, through `admission_boundary.authenticate_only`. All three billable handlers call `admission_boundary.admit(..., reserve=True)` at exactly one place each. `_process_v1_idempotent` keeps its Phase 5C order (claim → admit). `_process_v1_idempotent`'s and the document route's false docstrings rewritten. `_record_request_metadata` gained an `endpoint` parameter. `_check_api_key` (dead second gate) deleted; `_constant_time_equal` delegates to the single implementation. `/v1/ready` now reports the admission stack. 400 envelope carries the caller's `X-Request-Id`. |
| `api/routes/async_api.py` | Same admission path; `_metered_api_key_guard_async` authenticates without reserving; `POST /v1/documents` reserves exactly once after the claim. Job `request_id` aligned with the engine id; observability rows added for submission, worker success and worker failure with explicit endpoints. |
| `backend/auth/async_jobs.py` | **Engine-cache fix (audit C3)**: the per-module lookup-raw/store-normalized cache was removed; the store now delegates to `backend.auth.gate._session_factory()` (the canonical, locked, normalize-first cache) after a one-shot schema ensure. `complete_job()` accepts `request_id` for Phase 5I id alignment. |
| `backend/auth/idempotency.py` | Same engine-cache delegation fix. |
| `backend/auth/api_keys.py` | Same engine-cache delegation fix (the management plane had the identical latent defect). |
| `api/status.py` | `QUOTA_EXHAUSTED` → `PROCESSING` (retryable; was wrongly `INVALID_INPUT`). `RATE_LIMITED` added → `PROCESSING`. No other mapping changed. |
| `api/main.py` | 500 and 429 now emit the full deterministic envelope (`api_status` / `api_status_label` / `retryable` / `request_id`). No traceback or internals; type name still logged server-side only. |
| `api/schemas.py` | `DeveloperReadyResponse.admission` (additive, optional). |
| `frontend/functions/v1/[[path]].js`, `frontend/functions/api/[[path]].js` | Forward `x-platrixa-api-key`, `x-platrixa-management-token`, `idempotency-key`, `x-request-id`. Nothing is ever injected. |
| `frontend/web/lib/platrixa-proxy.ts` | Same forwarding; the server-side operator key is now a **fallback only** (never overwrites a caller key); contract response headers (`Idempotent-Replayed`, `Retry-After`, `X-Platrixa-Error`, `X-RateLimit-*`) pass back to the browser. |
| `DEPLOYMENT.md` | New admission/quota variable table, the deterministic identity-precedence table, and the health-vs-readiness section (audit D1). |
| `scripts/fte_fyjc_85_phase5i_admission_edge_test.py` | **NEW** — 70 anti-vacuous checks (sections A–J). |
| `scripts/fte_fyjc_86_phase5i_proxy_contract_test.py` | **NEW** — 11 checks driving the real proxy modules through Node. |
| `scripts/fte_fyjc_62_hosted_api_boundary_test.py` | Q1 pin updated to the corrected readiness contract + 4 new assertions (55 → 59 checks). |
| `scripts/fte_fyjc_65_hosted_api_security_test.py` | G3 updated to verify the single comparison implementation in its new home (same security property). |
| `scripts/fte_fyjc_77_six_state_api_status_test.py` | C-section pin updated for the `QUOTA_EXHAUSTED`/`RATE_LIMITED` mapping. |
| `scripts/fte_fyjc_79/80/81/83_*.py` | Cache-reset helpers updated to the canonical cache (the per-module `_session_factory_cache` they poked no longer exists). |

Untouched (pre-existing user work preserved): `.env`,
`backend/model_provider/*`, `docs/FYJC_15H_*`, `docs/fyjc_bk_*`,
`frontend/web/app/developer/page.tsx`, `frontend/web/components/developer-section.tsx`,
`scripts/fte_fyjc_66_metered_gate_test.py`, `scripts/fte_fyjc_76_phase24_foundation_scout.py`,
`scripts/verify_github_security_integration.sh`.

---

## 2. Admission flow

### Before

```
request
  └─ _metered_api_key_guard ── _check_api_key (Phase 15, separate gate)
                          └── gate.resolve_tenant      ← authenticates, never reserves
  └─ route body
       ├─ /v1/process        : claim ── authorize_request()   [1 unit]  ← per-route memory
       ├─ /v1/process/document: (nothing)                      [0 units] ← the bug
       ├─ /v1/documents      : claim ── authorize_request()   [1 unit]  ← per-route memory
       └─ everything else    : nothing                        [0 units]
```
Two independent credential gates, a dead `_check_api_key`, and reservation
chosen per call site.

### After

```
request
  └─ _metered_api_key_guard ── admission.authenticate_only()   ← authentication only
                                  └─ deterministic precedence (Phase 15 → Phase 16 → open)
  └─ route body (billable routes only)
       ├─ idempotency claim  (POST /v1/process, POST /v1/documents)
       ├─ cheap input validation (POST /v1/process/document)
       └─ admission.admit(reserve=True)   ← THE single reservation choke point
            └─ gate.authorize_request()   (atomic SQL: authenticate + one unit)
  └─ processing → result → observability append (endpoint-tagged)
```
Authentication always precedes processing; the reservation exists in exactly
one function, called at exactly one place per billable route, and the
per-route charge table is asserted by the suite.

---

## 3. Quota proof

Measured by `fte_fyjc_85` section A: the metering gate is stubbed with a
**counting** `authorize_request`, so every reservation attempt is observed.

| Route | Before (audit measurement) | After (Phase 5I) | Suite check |
|---|---|---|---|
| `POST /v1/process` | 1 | **1** | A1 |
| `POST /v1/process/document` | **0** ← C1 | **1** | A2 |
| `POST /v1/documents` (async) | 1 | **1** | A3 |
| `GET /v1/capabilities` | 0 | 0 | A4 |
| `GET /v1/health` | 0 | 0 | A5 |
| `GET /v1/ready` | 0 | 0 | A5 |
| `POST /v1/webhook-endpoints` | 0 | 0 | A6 |
| `POST /v1/process/document`, invalid input (400) | 0 | 0 | A7 |
| `POST /v1/process`, malformed body (400) | 0 | 0 | A8 |
| `POST /v1/process` with replayed `Idempotency-Key` | 0 (claim short-circuits) | **0** | B2 |
| `POST /v1/process` with conflicting key reuse (409) | 0 | **0** | B3 |
| `POST /v1/process`, quota exhausted (429) | 0 | **0** | C1/C6 |
| `POST /v1/process/document`, quota exhausted (429) | 0 | **0** | C4/C6 |
| Any route, metering store down (503) | 0 | **0** | C5/C6 |
| Unauthenticated (401) | 0 | **0** | D3/D11 |

Anti-vacuous properties:

* **A9** the total number of reservations across the whole route exercise is
  exactly 3 — one per billable call. A silently duplicated `admit()` call
  fails.
* **A10** static check: neither `api/routes/developer.py` nor
  `api/routes/async_api.py` contains `authorize_request(` or
  `metered_gate.reserve_unit(` — reservation cannot be re-added at a route.
* **C6** after every rejection class (auth, quota, store-down) the reservation
  counter is still 0: a refused request never leaves a phantom reservation.
* **B2** replay is proven with the real route decision order — the counting
  gate is not reached at all, and the response carries
  `Idempotent-Replayed: true`.

Billing semantics deliberately preserved (documented Phase 16 policy):
an admitted request that fails downstream **keeps** its reservation; a
rejected request (400/401/409/429/503) never gets one.

---

## 4. Proxy proof

Real modules, real forwarding path, `fetch` stubbed (`fte_fyjc_86`).

**Cloudflare Pages `functions/v1/[[path]].js`**

| Header | Before | After |
|---|---|---|
| `x-platrixa-api-key` | dropped | **forwarded unchanged** (P1) |
| `x-platrixa-management-token` | dropped | **forwarded unchanged** (P2) |
| `idempotency-key` | dropped | **forwarded unchanged** (P3) |
| `x-request-id` | dropped | **forwarded unchanged** (P3) |
| absent identity headers | — | **stay absent; nothing fabricated** (P4) |

The `/api` function received the identical allowlist (P5a).

**Next dev/preview `lib/platrixa-proxy.ts`**

| Case | Before | After |
|---|---|---|
| caller tenant key + operator key configured | caller's key **overwritten** by the operator key → wrong tenant's usage, no error | **caller's key forwarded unchanged**; operator key not substituted (N1) |
| management token | dropped | forwarded unchanged (N2) |
| `idempotency-key` | dropped | forwarded unchanged (N2) |
| no caller key + `PLATRIXA_API_KEY` set | operator key attached | operator key attached **as an explicit fallback** — documented dev-only behaviour (N3) |
| no caller key, no operator key | — | **nothing fabricated** (N4) |
| `Idempotent-Replayed` / `Retry-After` / `X-Platrixa-Error` | swallowed | **passed back to the browser** (N5) |
| target URL | fixed-target | unchanged, fixed-target (N6) |

Consequence: tenant identity now survives `browser → proxy → backend`
unchanged, management credentials stay management credentials, and an
operator key is never silently used for tenant traffic.

---

## 5. Cache proof

Audit C3 found `idempotency.py` and `async_jobs.py` had reintroduced the
engine-cache defect `gate.py` documents as fixed (lookup with the raw URL,
store under the normalized URL ⇒ a new pool per call).

Five successive `_session_factory()` calls per store, with
`PLATRIXA_METERING_DATABASE_URL=postgresql://…` (the documented scheme), with
`create_engine` counted:

| Store | Engines built over 5 calls | Same factory object as gate |
|---|---|---|
| `gate` (canonical) | **1** | — (E1) |
| `idempotency` | **1** (was 5) | **yes** (E3) |
| `async_jobs` | **1** (was 5) | **yes** (E5) |
| `api_keys` (management plane) | **1** | delegates to gate (E6) |

* **E2/E4** engine count, not "a cache dict exists": the assertion is on
  `create_engine` invocations.
* **E7** the normalized URL is the one stored — the original bug's trigger.
* **E8** 12 concurrent cold-start callers still build exactly **1** engine
  (the lock + double-check in the canonical factory).
* **E9** the one-shot DDL engine used for `CREATE TABLE IF NOT EXISTS` is
  **disposed** after the schema ensure; the shared pool is owned by the gate
  for the process lifetime.
* There is now exactly **one** engine cache in `backend/auth` (the gate's).
  No third implementation was added.

Regression safety: `fte_fyjc_79` (idempotency), `fte_fyjc_81` (async) and
`fte_fyjc_85` (cache section) were each run **3 extra times** — 9 repeat runs,
all green (§10).

---

## 6. Identity precedence (Phase 15 / 16)

Deterministic, single-boundary, enforced in `backend/auth/admission.py` and
documented in `DEPLOYMENT.md`:

| Mode | Condition | `/v1` accepts | Charged |
|---|---|---|---|
| `phase15-shared-key` | `PLATRIXA_DEV_API_KEY` set | **only** the exact shared key (constant-time, byte-for-byte) | no unit (no quota store in this mode) |
| `metered-tenants` | no Phase 15 key, metering store set | **only** Phase 5G tenant keys | exactly one unit per billable request |
| `open-anonymous` | neither set | any caller (documented zero-config local dev) | no unit |

Tests (`fte_fyjc_85` section D):

* **D1/D12** the shared key authenticates in Phase 15 mode, at HTTP level too.
* **D2/D11** a Phase 16 tenant key is **rejected (401)** while the Phase 15 key
  is configured — the audit's lockout bug is closed by making the precedence
  deterministic and explicit rather than incidental.
* **D3/D4** missing and wrong keys return the same indistinguishable reason.
* **D6/D7** in metered mode tenant keys work, missing keys are refused.
* **D9/D10** zero-config stays open-anonymous — the documented Phase 13
  contract is unchanged.
* **D5/D8/D10** the mode is a deterministic string, never guessed.
* Suite 65 **B1–B8** still pass: "no key configured → open", "missing key →
  401", "trailing-space key → 401" (raw comparison preserved — a
  whitespace-insensitive compare would have widened the boundary; this was
  caught by that suite and fixed).

No configuration produces unauthenticated *and* metered access, cross-tenant
identity, or a silent operator-key fallback.

---

## 7. Readiness

`GET /v1/ready` now reports **production admission readiness**, not merely
"the provider object can be built":

* `status` = ready only when the provider is available/loadable **and**
  `admission.production_ready` is true (a credential boundary exists and the
  metering store is reachable).
* `admission` block: `mode`, `phase15_shared_key_configured`,
  `metering_configured`, `metering_store_available`,
  `idempotency_supported`, `async_documents_supported`, `quota_enforced`,
  `production_ready`.
* `reason` names the missing piece ("admission not configured: no
  PLATRIXA_DEV_API_KEY and no metering store (zero-config open mode)").
* **No secrets**: I7/I8/I9 prove an unreachable store whose exception message
  contains `postgres://secret-user:secret-pw@host/db` does **not** leak it, and
  that no key or token material appears in the body.

`GET /v1/health` is unchanged: liveness only, touches nothing, no DB, no
provider, no model load. Suite 62 **Q1d** and I1 assert the distinction: health
green while ready is honestly not_ready.

Suite 62's Q1 pin was updated (it previously asserted "ready whenever the
provider is loadable", which is exactly the production bug) and extended to
assert the corrected semantics in both directions (55 → 59 checks, all pass).

---

## 8. Error contract

| Condition | Before | After |
|---|---|---|
| `QUOTA_EXHAUSTED` (429) | `api_status: INVALID_INPUT`, label "Invalid input", `retryable: false` | **`PROCESSING`, `retryable: true`**, code unchanged (`fte_fyjc_85` C2/C3/F3) |
| `RATE_LIMITED` (429) | bare `{code, message}`, no state, no retryability | full envelope + `PROCESSING`/`retryable: true` + correlation id (F5) |
| `400 REQUEST_MALFORMED` | envelope present, **no `request_id`** | envelope + caller's `X-Request-Id` (F1/F2) |
| `500` | `{"detail": "internal error"}` — no envelope at all | `INTERNAL_ERROR` envelope with `api_status: FAILED`, `retryable: false`, correlation id, **no traceback** (F6/F7) |

The six-state processing contract is untouched: only the transport-error
mapping for two codes changed, and it changed in the honest direction — a
quota window that resets is retryable, and a client that sent nothing wrong is
not "invalid input". `success`/`VERIFIED` semantics are unaffected; the
false-VERIFIED gate suite still reports `ALL PASS - false_VERIFIED == 0`.

---

## 9. Request tracing

| Path | Before | After |
|---|---|---|
| `POST /v1/process` (sync) | correlation id honoured; observability row written | unchanged |
| `POST /v1/process/document` | **no observability row** | row written, `endpoint="/v1/process/document"` (G1–G3) |
| `POST /v1/documents` (submit) | **no observability row** | row written with the correlation id, `api_status: PROCESSING` (G5/G6) |
| job execution (worker) | none | row written under the engine request id, `endpoint="/v1/documents(worker)"` |
| `GET /v1/developer/requests/{rid}` for an async request | **404** | the id submitted is the id stored and reported, so the lookup composes (G7) |

Idempotency keys and request ids remain distinct concepts: the idempotency key
still scopes the *creation* request and its replay store, while `request_id` is
the correlation id used by the observability log and the result envelope. The
worker aligns the job's stored `request_id` with the engine id via
`complete_job(..., request_id=...)` so one logical request has one id end to
end.

---

## 10. Tests

Every suite executed against the **final** tree. Nothing inferred.

**New Phase 5I suites**

| Suite | Result |
|---|---|
| `fte_fyjc_85_phase5i_admission_edge_test` | **70/70 checks passed** (sections A quota table · B replay/conflict · C rejections · D precedence · E engine cache · F error contract · G request tracing · H key exposure · I readiness · J tenant isolation) |
| `fte_fyjc_86_phase5i_proxy_contract_test` | **11/11 checks passed** (real Pages functions + real Next proxy through Node) |

**Phase 5A–5H regression**

| Suite | Result |
|---|---|
| `fte_fyjc_77_six_state_api_status_test` (5A) | 53/53 PASS |
| `fte_fyjc_78_phase5b_capability_discovery_test` (5B) | 50/50 PASS |
| `fte_fyjc_79_phase5c_idempotency_test` (5C) | 59/59 checks passed — ALL PASS |
| `fte_fyjc_80_phase5d_result_contract_test` (5D) | 54/54 PASS |
| `fte_fyjc_81_phase5e_async_documents_test` (5E) | 52/52 PASS |
| `fte_fyjc_82_phase5f_openapi_contract_test` (5F) | 26/26 PASS |
| `fte_fyjc_83_phase5g_api_key_lifecycle_test` (5G) | 44/44 checks passed |
| `fte_fyjc_84_phase5h_observability_test` (5H) | 45/45 checks passed |

**Boundary, metering, security, model**

| Suite | Result |
|---|---|
| `fte_fyjc_59_status_contract_test` | 20/20 passed |
| `fte_fyjc_61_developer_interface_test` | 65/65 passed |
| `fte_fyjc_62_hosted_api_boundary_test` | 59/59 passed (after the Q1 readiness-pin update) |
| `fte_fyjc_65_hosted_api_security_test` | 47/47 passed |
| `fte_fyjc_66_metered_gate_test` | 52/52 PASS |
| `fte_modal_resource_profile_test` | 68 checks — 68 passed, 0 failed |
| `fte_fyjc_57_remote_provider_test` | 43 PASS, 0 FAIL |
| `fte_fyjc_58_hf_gradio_transport_test` | 49 PASS, 0 FAIL |
| `fte_sec_02_api_authz_admission_test` | 19/19 passed |
| `fte_sec_03_webhook_ssrf_seal_test` | 24/24 passed |
| `fte_sec_04_error_disclosure_test` | 16/16 passed |
| `fte_invoice_false_verified_gate_test` | ALL PASS — false_VERIFIED == 0 |
| `fte_fyjc_grounding_verifier_test` | 16/16 passed |
| `security_baseline_test` | **16/16 suites passed — "BASELINE HOLDS"** (api_contract 6 · financial_truth 4 · invariant 1 · security 5) |

**Repeats for the engine-cache regression** (the 2026-09-29 incident class):
`fte_fyjc_79`, `fte_fyjc_81`, `fte_fyjc_85` each run 3 additional times —
9 repeat runs, every one green (59/59, 52/52, 70/70).

**Regressions found and fixed during this phase** (both caught by existing
suites, not by the new ones): (a) a `.strip()` in the new boundary made a
trailing-space key acceptable — caught by suite 65 B5, fixed to raw
comparison; (b) a whitespace guard placed before the mode branches broke the
zero-config open contract — caught by suite 85 D9 and suite 65 section A,
fixed by moving the guard inside the Phase 15 branch.

---

## 11. Deferred work

Explicitly **not** done in 5I:

1. **5J (bulk)** and **5K (queue/worker redesign)** — out of scope by brief.
2. **Async worker lease renewal** (audit H2): `JOB_LEASE_SECONDS = 120` with no
   heartbeat — a job slower than the lease can still be processed twice. A 5I
   fix would change worker scheduling semantics (5K territory).
3. **Async worker startup recovery** (audit H3): `ensure_worker_started()` still
   runs only on submission, so stranded `QUEUED`/expired-lease jobs wait for an
   unrelated request.
4. **Webhook delivery** (audit H4): retryable failures still emit no webhook
   (`EVENT_BY_API_STATUS` has no `PROCESSING` key) and `document.processing`
   is still unreachable; single-attempt delivery and no delivery record remain.
   Not touched — webhook redesign was out of scope; only the parts needed for
   correct admission (observability rows, id alignment) were changed.
5. **Document retention** (audit M5): `platrixa_async_jobs.request_json` still
   has no TTL/purge policy.
6. **Per-key usage** (audit M4) and **capability_id dead column** (audit M3):
   observability read-model gaps, outside the admission boundary.
7. **Connection-pool sizing** across processes/instances, and the
   `/api/v1` rate-limit bucket behaviour behind a CDN (needs an operator
   decision on `PLATRIXA_TRUSTED_PROXY_COUNT`, now documented).
8. **Model transport production gates** from the earlier phase remain
   `NOT_MEASURED` (no build host / no `llama_cpp`), and **no live deployment
   verification** has been performed: every result here is from the test
   harness against real route, store and proxy code.

---

## 12. Production status

**NOT_READY.**

What 5I establishes: one admission path, one reservation choke point, a
deterministic and tested credential precedence, no per-route charge
discrepancy, working end-to-end request/observability correlation, a coherent
error contract for 400/429/500/quota, identity-safe proxies, a single pooled
engine per store, minimized key material, and a readiness probe that reports
the truth. Every regression and security suite is green, including three repeat
runs of the suites that previously exhibited the pool-leak incident.

What it does not establish: the async worker's lease and startup defects can
still duplicate or strand work; webhook delivery is still incomplete; document
retention is unbounded; and nothing has been exercised against a real
deployment, a real database, or the model provider. Phase 5I made the boundary
coherent and verifiable — it did not make the platform production-ready.
