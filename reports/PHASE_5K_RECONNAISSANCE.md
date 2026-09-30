# Phase 5K — Reconnaissance

Read-only survey performed **before** any 5K edit. Source of the plan below.

## 1. Worktree state

Branch `main`, HEAD `73bfcc9` (5J). **11 pre-existing user modifications**
preserved and untouched: `.env`, `DEPLOYMENT.md`,
`backend/model_provider/{__init__,remote_hf}.py`, `docs/FYJC_15H_COVERAGE.md`,
`docs/fyjc_bk_15h_{coverage,replay_fixtures}.json`,
`frontend/web/app/developer/page.tsx`,
`frontend/web/components/developer-section.tsx`,
`scripts/fte_fyjc_76_phase24_foundation_scout.py`,
`scripts/verify_github_security_integration.sh`, plus all untracked files.
`scripts/fte_fyjc_84_phase5h_observability_test.py` is **untracked** (not in
git) — it exists on disk and passes, but is not version controlled.

## 2. Real PostgreSQL is available

`pgserver` is already the repository's supported embedded-PostgreSQL
tooling (used by `fte_fyjc_66`). Verified here by starting a server and
round-tripping a row: **`PG_REAL_OK`**. Section 22 (real-DB integration
testing) is therefore **satisfiable** — no blocker, and concurrency/fencing
behaviour can be proven against real PostgreSQL rather than asserted from
reading SQL.

## 3. Job store: `backend/auth/async_jobs.py` (790 lines)

### Schema (`_DDL`, line 229) — table `platrixa_async_jobs`

| column | note |
|---|---|
| `job_id` PK, `tenant_id`, `result_id` | tenant-scoped, indexed |
| `request_hash`, `request_json` | the document payload (`request_json` holds the raw document — see §9) |
| `status` | `QUEUED` / `PROCESSING` / `COMPLETED` / `FAILED` |
| `result_json`, `error_json`, `http_status`, `retryable` | terminal payload |
| `request_id` | correlation id |
| **`lease_expires_at`** | the **only** lease field |
| `created_at`, `updated_at` | |

**No `lease_owner`, no `lease_generation`, no `attempt_count`,
no `max_attempts`, no `next_attempt_at`.** Every one of these is a required
5K addition.

Second table `platrixa_webhook_endpoints` stores registration only
(`webhook_id, tenant_id, url, events, secret_sealed`). There is **no
delivery-attempt table at all**.

### Existing claim — already atomic

`claim_next_job()` (line 462) is a single
`UPDATE … WHERE job_id IN (SELECT … FOR UPDATE SKIP LOCKED) … RETURNING`.
PostgreSQL arbitrates concurrent claimers, so **two workers cannot claim
the same job**. This satisfies §5 as written; it must be preserved, and the
claim must additionally stamp ownership so §7 fencing is possible.

It already reclaims `PROCESSING` jobs whose lease expired — the crash-recovery
primitive exists, but nothing renews the lease, so any job slower than
120 s is re-claimed *while still legitimately running*.

### **F-1 — stale-worker commit is NOT fenced (critical, §7)**

`complete_job` (line 508) and `fail_job` (line 560) guard only on
`WHERE job_id = :j AND status = 'PROCESSING'`. They never verify that the
committing worker still owns the lease. Concretely:

1. worker A claims; lease expires;
2. worker B claims and starts processing (status is again `PROCESSING`);
3. worker A finishes and calls `complete_job` — the predicate
   `status='PROCESSING'` **is true**, so A overwrites B's authoritative
   result.

This is exactly the invariant §7 forbids: *a worker that no longer owns the
lease can publish its result.* Requires an ownership condition
(`lease_owner` + `lease_generation`, or equivalent).

### **F-2 — no lease renewal (§6)**

No renewal function exists. `JOB_LEASE_SECONDS = 120` (line 57) is a fixed
lease with no keepalive, so a legitimately slow job is duplicated at 120 s.

### **F-3 — worker starts ONLY on submission (§8)**

`ensure_worker_started()` is called from exactly one place — line 817, inside
the `POST /v1/documents` handler. Consequence: **a job created while no
worker runs stays `QUEUED` forever** until a *new submission* happens to
start the thread. There is no startup recovery scan and no scheduler.

The loop itself (`_worker_loop`, line 258) is `while True` + `sleep(0.5)`
polling, single-threaded and sequential.

### **F-4 — `document.processing` is unreachable (§15)**

`EVENT_PROCESSING` is declared (line 64) and listed in `ALL_EVENTS`, but
**never emitted anywhere** in the repository. `_emit_webhooks` (line 521)
maps only a *terminal* `api_status` through `EVENT_BY_API_STATUS`, so a
registered subscriber can never observe a `document.processing` event.

### **F-5 — webhook delivery is single-attempt and non-durable (§13/§14)**

`_deliver_webhooks` (line 539) posts once per endpoint in a fire-and-forget
daemon thread. On any exception it logs and gives up — the code comment
concedes *"Single attempt — no retry queue exists"*. No delivery status,
attempt count, `next_attempt_at`, or last HTTP status is persisted.

### **F-6 — retry is a boolean with no budget (§9/§10)**

`retryable` is a single BOOLEAN, and `fail_job` writes terminal `FAILED`
regardless of it. So a provider outage **exhausts the job on the first
failure** — the flag is informational only, never acted on. There is no
attempt counter, no backoff, and no `RETRY_WAIT` state.

### **F-7 — result uniqueness is unenforced (§11)**

`result_id` is a plain indexed column with **no unique constraint**. A
double execution (reachable via F-1/F-2) can overwrite `result_json` in
place rather than being rejected, so conflicting terminal results cannot
even be detected.

## 4. Canonical processing path

`api/routes/async_api.py::_process_job` (line 291) is the worker entry. It
calls the **existing document pipeline** (document understanding →
candidate IR → schema → grounding → authorities) and serializes with the
Phase 5D `build_process_result`. The async layer owns scheduling only.
**5K must not touch this function's pipeline calls.**

## 5. Bulk durable path

5J bulk is **synchronous only** — there is no durable bulk job path, and
§13 explicitly defers changing the 5J contract. 5K therefore applies to the
document/job plane only.

## 6. Quota

Reservation happens **once**, in the API admission path, before job creation
(`async_api` uses the 5I `admit()` choke point). The worker never calls
`admit`/`reserve_units`. Preserved by construction; 5K adds a regression
test to prove a worker retry cannot re-charge.

## 7. Observability

`GET /v1/developer/requests/{request_id}` reads the request log; the worker
writes a row at endpoint `/v1/documents(worker)` using the engine request id
(async_api line 381). The chain `request_id → job_id → result_id` is
partially wired: `_resolve_engine_request_id` (line 271) aligns ids, and 5I
already closed the submission-side 404. 5K must preserve this and add
`job_id` correlation.

## 8. Plan derived from the findings

| Fix | Addresses | Approach |
|---|---|---|
| `lease_owner` + `lease_generation` columns, stamped at claim | F-1, §5, §7 | additive `ALTER TABLE … IF NOT EXISTS` migration alongside `_DDL` |
| Ownership-conditional `complete_job`/`fail_job` | **F-1**, §7 | `WHERE … AND lease_owner = :o AND lease_generation = :g`; rowcount 0 ⇒ rejected |
| `renew_lease()` + bounded renewal thread | **F-2**, §6 | renew at lease/3, stop on completion, never spawn unbounded tasks |
| `attempt_count` / `max_attempts` / `next_attempt_at`, `RETRY_WAIT` | **F-6**, §10 | extend the state machine; bounded exponential backoff |
| Retry classifier over existing status/reason codes | §9 | reuse `api.status` mapping, never infer from HTTP status alone |
| `UNIQUE (result_id)` | **F-7**, §11 | migration; one canonical result per job |
| Startup recovery scan + bounded-concurrency worker | **F-3**, §18 | claim on boot, not only on submission; bounded pool, defined overload |
| `platrixa_webhook_deliveries` table + retry loop | **F-5**, §13, §14 | durable rows, bounded retry, `Retry-After` honoured |
| Emit `document.processing` at claim | **F-4**, §15 | reuse the existing event vocabulary; no new names |
| Quota-not-recharged-on-retry test | §12 | regression test |

**Untouched:** `backend/kernel`, `backend/rules`, `backend/grounding`,
`backend/maths`, `core`, `formula_engine`, `platrixa`. The worker keeps
calling the existing pipeline; 5K adds scheduling, leasing and delivery
around it and changes no financial-truth logic.
