# Phase 5K — Production Queue / Worker Reliability

**Status: `READY_FOR_DEPLOYMENT_VERIFICATION`**

Not production-ready. No live deployment, no real model provider, and no
multi-process/real-load run were exercised. Everything below is proven
against a **real PostgreSQL server** (embedded `pgserver`, the repository's
supported tooling) plus in-process worker tests. See *Deployment
limitations*.

---

## 1. Architecture

```
POST /v1/documents
      │  Phase 5I admission — ONE billable reservation
      ▼
durable row: QUEUED
      │
      │  worker claims (atomic, stamps lease_owner + lease_generation)
      ▼
  PROCESSING ──── lease renewal thread (1/3 lease cadence) ────┐
      │                                                        │
      │  EXISTING document pipeline (unchanged)               │
      │  → CandidateSemanticIR → schema → grounding →         │
      │    deterministic authorities                           │
      │                                                        │
      ├─ success ─→ fenced COMPLETED commit ─→ durable result ─┤
      ├─ permanent failure ─→ terminal FAILED                 │
      └─ retryable failure ─→ RETRY_WAIT (bounded backoff) ───┘
                                    │
                                    ▼
                       re-claim when next_attempt_at passes

webhook: terminal/processing transition → durable PENDING delivery row
         → attempt → DELIVERED | RETRYING (bounded) | FAILED
```

5K adds **scheduling, leasing, fencing and delivery** around the existing
pipeline. It does not touch the pipeline.

## 2. Final job state machine

Reuses the pre-existing names; `RETRY_WAIT` is the only addition, and it
exists because a retryable failure must be *deferred*, not lost.

```
QUEUED ──claim──▶ PROCESSING ──success──▶ COMPLETED   (terminal)
                      │
                      ├── permanent failure ─────▶ FAILED  (terminal)
                      ├── retryable + budget left ─▶ RETRY_WAIT ──backoff──▶ QUEUED
                      └── retryable + budget spent ▶ FAILED  (terminal)

PROCESSING ──lease expiry──▶ claimable again (crash recovery)
```

Invariants enforced **in SQL**, not in application code:

- a terminal job is never re-claimed (the claim filters on status);
- only the current lease owner may transition the job;
- `lease_generation` increments on **every** claim, so a worker that
  loses the lease can never present a valid token again.

`RETRY_WAIT` is *not* a new public API state — it is an internal job
status. The tenant-visible `api_status` vocabulary is unchanged.

## 3. Lease semantics

Columns added (idempotent `ALTER TABLE … IF NOT EXISTS` migration, so an
upgraded store converges to the same schema as a fresh one):

| column | purpose |
|---|---|
| `lease_owner` | identity of the worker currently holding the job |
| `lease_generation` | monotonic fencing token, bumped on every claim |
| `lease_expires_at` | *(pre-existing)* lease deadline |
| `attempt_count` / `max_attempts` | bounded retry budget |
| `next_attempt_at` | backoff gate |

**Claim is atomic** — the pre-existing
`UPDATE … WHERE job_id IN (SELECT … FOR UPDATE SKIP LOCKED)` is preserved
and extended to stamp ownership and consume one attempt. Two workers
cannot claim the same job (proven: 8 concurrent claimers → 1 winner, §16).

**Fencing is the substantive fix.** Before 5K, `complete_job`/`fail_job`
guarded only on `status='PROCESSING'`, so:

1. worker A claims, lease expires;
2. worker B claims (status is `PROCESSING` again);
3. worker A finishes → its commit **matched** and overwrote B's result.

Now every terminal commit requires `lease_owner` **and**
`lease_generation` to still match, and a commit with **no** ownership
proof is *refused* rather than allowed. Removing the pre-5K predicate was
the single most important change in this phase.

## 4. Lease renewal

`_LeaseRenewal` is a context manager wrapping the whole job body:

- renews at **1/3** of the lease, so two consecutive failures are absorbed
  before expiry;
- renewal is conditional on still owning the lease — a fenced-out worker
  cannot extend a lease it no longer holds;
- `renewal.lost` is checked **before** the result commit, so a worker that
  lost its lease discards its work instead of publishing it;
- exactly one daemon thread per in-flight job, started and stopped
  deterministically (`__exit__` joins it, including on exception).

Proven against real PostgreSQL: renewal extends the expiry, a renewed job
is not stealable, and the thread terminates (thread count returns to
baseline).

## 5. Startup recovery

The worker previously started **only on submission** — a job created
while no worker was running stayed `QUEUED` forever. Now:

- `_recovery_sweep()` runs on worker entry, reporting outstanding jobs and
  draining due webhook deliveries left by a previous process;
- the poll loop claims normally, so pre-existing and expired-lease jobs
  are picked up without waiting for another submission.

Proven (Test D): a job created with **no worker running** is discovered
and completed by a worker started afterwards.

The sweep deliberately only *counts* — reclaiming stays inside the atomic
`claim_next_job`, so no second racy reclaim path can double-claim.

## 6. Retry classification

One deterministic classifier (`classify_failure`), reused by the worker so
two call sites cannot disagree. **Retryability is never inferred from an
HTTP status alone.**

- **Retryable** (infrastructure; outcome genuinely unknown):
  `PROVIDER_UNAVAILABLE`, `MODEL_UNAVAILABLE`, `METERING_UNAVAILABLE`,
  `ASYNC_UNAVAILABLE`, `INTERNAL_ERROR`, `RESULT_PENDING`
- **Not retryable** (deterministic user-input outcome): `INPUT_INVALID`,
  `INPUT_MISSING`, `INPUT_AMBIGUOUS`, `REQUEST_MALFORMED`,
  `FILE_TYPE_UNSUPPORTED`, `VALIDATION_FAILED`, `GROUNDING_FAILED`,
  `UNAUTHORIZED`, batch-validation codes, and anything in
  `INVALID_INPUT`/`UNSUPPORTED` api_status
- **Unknown code → not retryable** (fails safe; a new deterministic
  rejection can never spin in a retry loop)

## 7. Retry limits and backoff

**These are engineering defaults, not measured production values.**

| parameter | value | env override |
|---|---|---|
| initial delay | 5 s | — |
| backoff factor | ×2 per attempt | — |
| maximum delay | 300 s | — |
| maximum attempts | 3 per job | `max_attempts` column |

Observed sequence: `5, 10, 20, 40 …` capped at 300.

A job whose budget is exhausted becomes **terminal `FAILED`** — retries
are always bounded.

## 8. Result idempotency

- `UNIQUE (result_id)` is enforced **at the database**
  (`uq_async_jobs_result_id`), created defensively: if pre-existing
  duplicates are found the constraint is skipped with a warning rather
  than aborting startup.
- The terminal commit is fenced, so a duplicate execution cannot overwrite
  an authoritative result.
- Proven: a stale second commit is rejected, the first result is
  unchanged, and a direct duplicate `INSERT` raises (Test B).

## 9. Quota semantics

Unchanged and proven intact. The **submission route** performs exactly one
admission (`admission_boundary.admit(...)`, one call site). The worker
never re-enters the admission/quota path, so N execution retries remain
**one billed request**. Both facts are asserted (one behavioural, one
structural, so a regression in either is caught).

## 10. Webhook delivery lifecycle

New `platrixa_webhook_deliveries` table (job, tenant, webhook, event,
status, attempt count, `next_attempt_at`, `last_attempt_at`,
`last_http_status`, `last_error`, `delivered_at`).

- A delivery row is persisted **before** the HTTP attempt, so a crash in
  between leaves a `PENDING` row the worker retries — previously the event
  was simply lost.
- The delivery id is **derived** from `(job_id, webhook_id, event)`, so a
  retried job cannot create a second delivery for the same subscriber.
- Rows reference the canonical persisted result; no raw document or
  financial payload is stored.

**Classification** (never a bare status check): 2xx delivered · 408/429
retry · other 4xx **permanent** · 5xx and network errors retry.
`Retry-After` is honoured within a bounded window (≤300 s) so a hostile
endpoint cannot park a delivery forever.

**Delivery semantics: at-least-once**, with deterministic event ids
(`sha256(job_id:event)`) so consumers can deduplicate. Exactly-once is
**not** claimed and is not proven.

## 11. `document.processing` lifecycle

`EVENT_PROCESSING` was declared in `ALL_EVENTS` but **never emitted
anywhere** in the repository. It is now emitted at **claim time** — the
only point at which "this document is being processed" is true — giving
`document.processing → document.completed | document.failed | …`. No new
event vocabulary was invented.

## 12. Observability

The `request_id → job_id → result_id` chain is preserved and tenant-scoped:
`get_job` and `get_result_record` both filter on `tenant_id`, and
cross-tenant reads return nothing (proven). The worker writes its outcome
row under the engine request id (5I behaviour, unchanged). No API keys,
tokens, or raw financial payloads are logged by any 5K code path.

## 13. Worker concurrency

Bounded by `WORKER_MAX_CONCURRENCY` (default 2, env
`PLATRIXA_WORKER_CONCURRENCY`): a fixed `Semaphore`, not a task per queued
job. Jobs are pulled only while a slot is free; **overload behaviour is
explicit** — excess `QUEUED` jobs wait durably rather than being dropped or
partially processed. Webhook retries drain in bounded batches
(`WEBHOOK_BATCH_SIZE = 20`).

No jobs/sec or latency figure is claimed; none was benchmarked.

## 14. Resource lifecycle

- Stores continue to delegate to the canonical Phase 5I engine cache.
  **5 sequential calls → 1 engine** re-verified for `gate`,
  `idempotency` and `async_jobs` after the worker integration.
- DDL engines are still created-and-immediately-disposed.
- The renewal thread and the worker loop both terminate deterministically
  (`_interruptible_sleep` + `stop_event` for prompt shutdown).

## 15. Security / trust-boundary audit

`git diff --stat` over `backend/kernel`, `backend/rules`,
`backend/grounding`, `backend/maths`, `core`, `formula_engine`,
`platrixa` is **empty**. No financial-truth logic was changed.

The worker calls the **existing** document pipeline. It does not compute
accounting, trust model output, or bypass CandidateSemanticIR, schema
verification, grounding, or the deterministic authorities. `VERIFIED` still
means "validated by the deterministic pipeline" — not tax, legal,
regulatory, or accounting compliance.

## 16. PostgreSQL integration results

`scripts/fte_fyjc_88_phase5k_queue_worker_test.py` — **83/83** against a
**real** PostgreSQL server (`pgserver`), not a mock.

| Required test | Result |
|---|---|
| A — worker crash during processing | PASS (F3: expired lease reclaimed; C4/C5) |
| B — crash after result persistence | PASS (E1–E4: no duplicate result) |
| C — stale worker | PASS (C6: commit **rejected**; C7: new owner accepted) |
| D — startup recovery | PASS (J1/J2: pre-existing job completed) |
| E — lease renewal | PASS (J6–J8: extended, not stealable) |
| F — webhook failure → retry | PASS (G9: 5xx → RETRYING) |
| G — webhook permanent failure | PASS (G10/G12: terminal, bounded) |

Also proven against real PostgreSQL: concurrent claim exclusivity, the
unique `result_id` constraint, lease fencing, and cross-tenant isolation.

## 17. Anti-vacuity — 8 mutations, 8 caught

Each guarantee was disabled in the implementation and the suite re-run.

| # | mutation | outcome |
|---|---|---|
| 1 | lease renewal disabled | **caught** (C2) |
| 2 | stale-worker ownership check removed | **caught** (C6, C7) |
| 3 | startup recovery removed | **caught** (J15) |
| 4 | classifier marks everything retryable | **caught** (D2, D3, D4) |
| 5 | webhook retries removed | **caught** (G9) |
| 6 | duplicate-result protection removed | **caught** (J11) |
| 7 | worker concurrency unbounded | **caught** (J3 — peak 6 vs cap 2) |
| 8 | worker re-charges quota on retry | **caught** (J13b) |

Three of these initially escaped and exposed **real test-coverage holes**,
which were closed rather than papered over: the worker loop had no
behavioural test at all (added section J), the uniqueness and quota
invariants were never asserted (added J11/J13), and the recovery check
matched the *definition* of `_recovery_sweep` rather than a call site
(re-anchored to the loop body).

## 18. Regression suites (exact results)

| Suite | Result |
|---|---|
| `fte_fyjc_88` (5K, real PostgreSQL) | **83/83** |
| `fte_fyjc_77` (5A six-state) | 53/53 |
| `fte_fyjc_78` (5B capabilities) | 50/50 |
| `fte_fyjc_79` (5C idempotency) | 59/59 |
| `fte_fyjc_80` (5D result contract) | 54/54 |
| `fte_fyjc_81` (5E async documents) | 54/54 |
| `fte_fyjc_82` (5F OpenAPI) | 26/26 |
| `fte_fyjc_83` (5G API keys) | 44/44 |
| `fte_fyjc_84` (5H observability) | 45/45 |
| `fte_fyjc_85` (5I admission) | 70/70 |
| `fte_fyjc_86` (5I proxy) | 11/11 |
| `fte_fyjc_87` (5J bulk) | 76/76 |
| `fte_fyjc_66` (metered gate) | 53/53 |
| `fte_fyjc_62` (boundary) | 59/59 |
| `fte_fyjc_65` (hosted security) | 47/47 |
| `fte_sec_01` grounding fail-closed | 24/24 |
| `fte_sec_02` authz/admission | 19/19 |
| `fte_sec_03` webhook SSRF seal | 24/24 |
| `fte_sec_04` error disclosure | 16/16 |
| `fte_sec_05` dependency policy | 20/20 |
| `fte_invoice_false_verified_gate` | PASS |
| `fte_modal_resource_profile` | 68/68 |
| `security_baseline_test` | 16/16 "BASELINE HOLDS" |

**Suites changed because the contract genuinely moved** (not to force
green):

- `fte_fyjc_81` (5E): `complete_job`/`fail_job` now require lease
  ownership, so the suite presents it; and a `retryable=True` failure now
  yields `RETRY_WAIT` rather than terminal `FAILED`. Both are the intended
  5K semantics, and new assertions (B6b, B8, B8b) pin them.
- `api/routes/developer.py`: four hardcoded `"VERIFIED"` string literals
  (introduced in 5J) violated the suite 62/65 "no status literals in
  executable route code" discipline. They now reference the status
  authority. This was a **latent 5J regression** the boundary suites
  caught.

## 19. Remaining risks

- Retry/backoff constants and the concurrency cap are **unmeasured
  defaults**.
- Multi-process behaviour is unverified: claim atomicity is proven
  per-database, but no two-process run was performed.
- `request_json` still stores the raw document (pre-existing, §20).
- A worker that loses its lease mid-processing discards its work; the job
  is re-run by the new owner. That is the correct trade (at-least-once),
  but it means duplicated provider work is possible by design.
- `fte_fyjc_84` remains **untracked in git** (it runs and passes, but is
  not version controlled).

## 20. Deployment limitations

No live deployment, real provider, or real load was exercised:

- real model provider verification: **NOT_MEASURED**
- multi-process / multi-instance worker contention: **NOT_RUN**
- throughput and latency: **NOT_MEASURED**
- webhook delivery against real endpoints: **NOT_MEASURED**

The system is therefore **not** production-ready; it is ready for
deployment verification, which is where those gaps get closed.

## 21. Deferred work

- **5K+**: raw document retention/TTL for `request_json` (pre-existing).
- **5K+**: per-key usage attribution for worker-executed jobs.
- **5L+**: asynchronous bulk (5J remains synchronous by design).
- **5L+**: webhook `Retry-After` semantics refinement, dead-letter queue
  and manual redelivery.
- Unchanged from the 5I/5J audits: model transport gates (llama.cpp/GGUF/
  Modal/HF ZeroGPU) remain unmeasured; frontend exposes no worker
  operational surface.
