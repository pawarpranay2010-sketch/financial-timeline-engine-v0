# Platrixa Developer API — Stable Contract (v1, private beta)

This document describes **what the deployed code implements**. Anything not
listed here is not part of the contract. The OpenAPI document served at
`/openapi.json` is generated from the same route definitions.

The deterministic kernel is the sole authority. `status`, `api_status` and
every accounting field are produced by the kernel; the transport layer maps and
forwards them and can never create or upgrade a state.

---

## 1. Authentication

| Header | Required | Notes |
| --- | --- | --- |
| `X-Platrixa-API-Key` | yes | Tenant credential. Server-derived tenant scope; never logged or echoed. |
| `Idempotency-Key` | no | Creation requests only. 16–200 characters from `A-Za-z0-9._~-`. |
| `X-Request-Id` | no | Correlation id echoed in the response / logs (bounded). |

A missing/invalid/revoked key is refused before any handler runs. A caller can
never supply a tenant id.

---

## 2. Endpoints

### Developer (tenant key)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/inference-jobs` | Create one durable inference job (single transaction or bounded batch). |
| `GET` | `/v1/jobs` | List your tenant's jobs, newest first (cursor paginated). |
| `GET` | `/v1/jobs/{job_id}` | Poll job status (batch jobs include a `batch` block). |
| `GET` | `/v1/results/{result_id}` | The authoritative result envelope (single) or batch envelope (batch). |
| `GET` | `/v1/results/{result_id}/transactions` | Batch only: per-transaction summaries, paginated. |
| `GET` | `/v1/results/{result_id}/transactions/{transaction_id}` | Batch only: one transaction's full envelope. |
| `POST` | `/v1/process` | Synchronous single transaction (unchanged pre-batch contract). |
| `GET` | `/v1/ready` | Admission readiness. |

### Device worker (tenant key; operated by the phone, not by developers)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/worker/jobs/claim` | Claim at most one runnable job for this tenant. |
| `POST` | `/v1/worker/jobs/{job_id}/lease` | Renew the lease (fenced). |
| `POST` | `/v1/worker/jobs/{job_id}/result` | Single: submit a candidate. Independent batch: submit all candidates in one fenced call. |
| `POST` | `/v1/worker/jobs/{job_id}/transactions/{transaction_id}/result` | Ordered batch: submit one link; returns the approved state for the next. |
| `POST` | `/v1/worker/jobs/{job_id}/failure` | Report that this generation produced no submittable candidate. |
| `POST` | `/v1/worker/jobs/{job_id}/release` | Return the claim to the queue because the device slot was busy (attempt budget refunded). |

### Owner (management token, never a developer key)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/developer/tenants` | Provision one tenant + key + quotas. |
| `GET` | `/v1/developer/api-keys` | List masked keys. |
| `POST` | `/v1/developer/api-keys/{key_id}/rotate` | Rotate a key (new raw key shown once). |
| `POST` | `/v1/developer/tenants/{tenant_id}/keys/{key_id}/revoke` | Revoke a tenant key. |
| `GET` | `/v1/owner/queue/status` | Queue depth, reconciliation, lease-derived workers. |
| `GET` | `/v1/owner/usage` | Per-tenant jobs, transactions, quotas, throttling events. |

---

## 3. Create a job

### 3.1 Single transaction (unchanged)

```json
{ "raw_input": "Purchased furniture for cash Rs. 15,000" }
```

`raw_input`: 1–2000 characters.

### 3.2 Batch

```json
{
  "mode": "independent",
  "transactions": [
    {"transaction_id": "tx-001", "input": "Started business with cash Rs. 10000"},
    {"transaction_id": "tx-002", "input": "Purchased goods for cash Rs. 2000"}
  ]
}
```

| Field | Rule |
| --- | --- |
| `mode` | `independent` (default) or `ordered`. |
| `transactions` | Non-empty list. Length ≤ `PLATRIXA_BATCH_MAX_TRANSACTIONS` (default 10, hard ceiling 50). |
| `transactions[].transaction_id` | `^[A-Za-z0-9._:-]{1,64}$`, unique within the batch. |
| `transactions[].input` | 1–2000 characters. |
| Summed input | ≤ `PLATRIXA_BATCH_MAX_INPUT_CHARS` (default 20,000). |
| Request bytes | ≤ `PLATRIXA_BATCH_MAX_BYTES` (default 262,144). |

`raw_input` and `transactions` are mutually exclusive.

### 3.3 Response (202)

```json
{
  "api_version": "v1",
  "request_id": "req-1",
  "job_id": "job_...",
  "result_id": "res_...",
  "status": "PROCESSING",
  "status_label": "Processing",
  "status_url": "/v1/jobs/job_...",
  "result_url": "/v1/results/res_...",
  "created_at": "2026-11-19T09:00:00+00:00",
  "mode": "independent",
  "transaction_count": 2,
  "batch_url": "/v1/results/res_.../transactions"
}
```

`mode`, `transaction_count` and `batch_url` are present **only** for batches.

### 3.4 Rejection codes (before admission; nothing is created or charged)

`REQUEST_MALFORMED`, `BATCH_EMPTY`, `BATCH_MALFORMED`, `BATCH_TOO_LARGE`,
`BATCH_TOO_MANY_TRANSACTIONS`, `BATCH_INPUT_TOO_LARGE`,
`BATCH_TRANSACTION_INVALID`, `BATCH_DUPLICATE_TRANSACTION_ID`,
`BATCH_MODE_INVALID`, `BATCH_AMBIGUOUS`, `IDEMPOTENCY_KEY_INVALID`.

Rejections carry `error.details` with **structural** information only (the
offending index, the limit, the caller's own duplicate ids).

---

## 4. Job status

```json
{
  "api_version": "v1",
  "job_id": "job_...",
  "request_id": "req-1",
  "status": "PROCESSING | RETRY_WAIT | COMPLETED | FAILED",
  "status_label": "...",
  "retryable": true,
  "reason_codes": [],
  "result_url": "/v1/results/res_...",
  "created_at": "...",
  "updated_at": "...",
  "batch": {
    "mode": "independent",
    "total_transactions": 2,
    "counts": {"verified": 1, "review_required": 0, "unsupported": 0,
               "failed": 1, "blocked": 0, "deferred": 0, "pending": 0},
    "transactions_url": "/v1/results/res_.../transactions"
  }
}
```

`batch` is present only for batch jobs. `RETRY_WAIT` is not a terminal verdict.

### 4.1 Job history — `GET /v1/jobs`

```http
GET /v1/jobs?limit=20&cursor=<opaque>&status=COMPLETED
X-Platrixa-API-Key: <tenant key>
```

```json
{
  "api_version": "v1",
  "tenant_id": "acme-books",
  "jobs": [
    {
      "job_id": "job_9f3c1d2a4b5c6d7e8f901234",
      "status": "COMPLETED",
      "status_label": "Completed",
      "api_status": null,
      "retryable": false,
      "kind": "inference",
      "created_at": "2026-11-19T09:30:00.123456+00:00",
      "updated_at": "2026-11-19T09:30:41.987654+00:00",
      "completed_at": "2026-11-19T09:30:41.987654+00:00",
      "transaction_count": 5,
      "batch": {"mode": "independent", "transaction_count": 5},
      "result_available": true,
      "result_url": "/v1/results/res_4b5c6d7e8f9012345678abcd",
      "reason_code": null,
      "request_id": "req-1"
    }
  ],
  "count": 1,
  "limit": 20,
  "order": "created_at DESC, job_id DESC",
  "has_more": true,
  "next_cursor": "eyJjIjoiMjAyNi0xMS0xOVQwOTozMDowMC4xMjM0NTYrMDA6MDAiLCJqIjoiam9iXzlmM2MxZDJhNGI1YzZk N2U4ZjkwMTIzNCIsInYiOjF9"
}
```

Guarantees:

- **Tenant-scoped from the key only.** No query parameter can select a tenant;
  a foreign job is never selected, so its existence is not revealed.
- **Stable keyset order** `created_at DESC, job_id DESC` — no `OFFSET`, so
  pages cannot skip or duplicate while new jobs are submitted.
- **No payloads.** `result_json`/`request_json` are never returned;
  `result_available` is a server-side projection and the result itself is
  fetched from `result_url`.
- **`api_status` is null for `COMPLETED`:** the authoritative outcome
  (`VERIFIED` / `REVIEW_REQUIRED` / …) lives in the result envelope, and the
  list does not re-read payloads to guess it. For `FAILED` it is mapped from
  the stored `reason_code`.
- Reading the list consumes **no quota**.

Pagination:

| Rule | Value |
| --- | --- |
| Default page size | 20 |
| Hard maximum | 100 (larger → `400 INPUT_INVALID`) |
| Cursor | opaque `next_cursor`; pass it back verbatim |
| Cursor expiry | none; cursors carry no authority |
| Malformed cursor | `400 INVALID_CURSOR` (never a silent restart at page 1) |
| `has_more` | false on the last page; `next_cursor` is then `null` |
| Status filter | `QUEUED`, `PROCESSING`, `RETRY_WAIT`, `COMPLETED`, `FAILED` |
| Invalid filter | `400 INVALID_STATUS_FILTER` |

Building a history screen: request `limit=20`, render `jobs`, keep
`next_cursor`, and request again only when the user scrolls/pages. Treat
`result_available` as "there is a result to fetch", not as "it passed".

---

## 5. Results

### 5.1 Single (canonical Phase 5D envelope)

Unchanged: `status` (engine verbatim), `api_status` (six-state mapping),
`engine_status`, `success`, `retryable`, `reason_codes`, `interpretation`,
`accounting`, `issues`, `grounding_issues`, `rule_evidence`, `evidence`,
`metadata`.

### 5.2 Batch envelope

```json
{
  "api_version": "v1",
  "request_id": "req-1",
  "job_id": "job_...",
  "batch_id": "job_...",
  "result_id": "res_...",
  "mode": "ordered",
  "batch_status": "COMPLETE | PARTIAL | HALTED",
  "status": "VERIFIED",
  "api_status": "VERIFIED",
  "engine_status": "VERIFIED",
  "success": true,
  "retryable": false,
  "reason_codes": [],
  "total_transactions": 3,
  "processed_transactions": 3,
  "verified_count": 3,
  "review_required_count": 0,
  "unsupported_count": 0,
  "failed_count": 0,
  "blocked_count": 0,
  "deferred_count": 0,
  "counts": {"total": 3, "processed": 3, "verified": 3, "...": 0},
  "state_chain": [
    {"batch_index": 0, "transaction_id": "tx-001",
     "prior_state_hash": null, "state_hash": "9f3c..."}
  ],
  "final_state_hash": "9f3c...",
  "created_at": "...",
  "completed_at": "...",
  "transactions": [
    {"batch_index": 0, "transaction_id": "tx-001", "status": "VERIFIED",
     "api_status": "VERIFIED", "engine_status": "VERIFIED", "error_code": null,
     "http_status": 200, "prior_state_hash": null, "state_hash": "9f3c...",
     "result_url": "/v1/results/res_.../transactions/tx-001"}
  ],
  "quota": {"transactions_admitted": 3},
  "metadata": {"authority": "deterministic-kernel", "mode": "ordered"}
}
```

Aggregation is deterministic and order-stable:

- `api_status` is `VERIFIED` only when **every processed** transaction is
  `VERIFIED`; otherwise it is the **first** non-verified transaction in batch
  order, carried verbatim;
- if nothing was processed (every transaction deferred), `api_status` is
  `FAILED` with `BATCH_NOT_PROCESSED` and `retryable: true` — nothing was
  evaluated, so resubmitting is meaningful;
- `success` requires `batch_status == COMPLETE`, every transaction processed,
  and every one `VERIFIED`;
- `batch_status` is `PARTIAL` when any transaction is `DEFERRED`, `HALTED` when
  any is `BLOCKED`, otherwise `COMPLETE`.

### 5.3 Per-transaction states

| State | Meaning |
| --- | --- |
| `PENDING` | not yet evaluated |
| `VERIFIED` | kernel approved; accounting present |
| `REVIEW_REQUIRED` | insufficient evidence/capability; a human must decide |
| `UNSUPPORTED` | outside the supported boundary |
| `INVALID_INPUT` | the input/candidate failed validation |
| `FAILED` | the engine reported failure (see `error_code`) |
| `BLOCKED` | ordered mode only: an earlier link was not `VERIFIED`, so this transaction was never evaluated (`BLOCKED_BY_PRIOR_UNVERIFIED_STATE`) |
| `DEFERRED` | admitted but not evaluated because the device budget ran out (`BATCH_BUDGET_EXHAUSTED`); resubmit it |

A per-transaction envelope is available at `result_url`. `BLOCKED` and
`DEFERRED` transactions have **no** fabricated envelope (`RESULT_NOT_READY`).

### 5.4 Pagination

`GET /v1/results/{result_id}/transactions?limit=50&offset=0`

`limit` is clamped to 1–200. The response reports `total`, `limit`, `offset`.

---

## 6. Limits and quotas

| Limit | Default | Configuration |
| --- | --- | --- |
| Transactions per batch | 10 | `PLATRIXA_BATCH_MAX_TRANSACTIONS` (1–50) |
| Batch request bytes | 262,144 | `PLATRIXA_BATCH_MAX_BYTES` |
| Batch summed input chars | 20,000 | `PLATRIXA_BATCH_MAX_INPUT_CHARS` |
| Device budget per batch | 180 s | `PLATRIXA_BATCH_MAX_SECONDS` |
| Pending jobs (global) | 100 | `PLATRIXA_QUEUE_GLOBAL_LIMIT` |
| Pending jobs (per tenant) | 10 | `PLATRIXA_QUEUE_PER_TENANT_LIMIT` |
| Requests per tenant window | 120 / 60 s | `PLATRIXA_TENANT_RATE_LIMIT`, `PLATRIXA_TENANT_RATE_WINDOW_SECONDS` |
| Monthly jobs per tenant | 100 (provisioning) | owner provisioning `monthly_limit` |
| Monthly transactions per tenant | 1000 | `PLATRIXA_TENANT_TX_MONTHLY_LIMIT`, owner override |

Two quota units are tracked independently: **jobs admitted** and
**accounting transactions admitted**. A batch of N transactions costs 1 job
unit and N transaction units.

---

## 7. Idempotency

- Scope: `(tenant, endpoint, key)`.
- Fingerprint for a single job: `raw_input`. For a batch: `mode` plus every
  `(transaction_id, input)` pair.
- Same key + same fingerprint → the original job reference (202,
  `Idempotent-Replayed: true`); no new job, no new charge.
- Same key + different fingerprint → `409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST`.
- A claim that is still in flight → `409`/`202` with `IDEMPOTENCY_REQUEST_IN_PROGRESS`
  (retry the same key shortly).
- Retention: 72 hours.
- Worker retries of an admitted job never re-charge.

---

## 8. Authentication, ownership and errors

- Results, jobs and per-transaction envelopes are tenant-scoped; a foreign id
  is the same non-disclosing `404` as a nonexistent one.
- The owner plane requires `X-Platrixa-Management-Token`; a developer key is
  `403` there.
- Errors use one envelope:

```json
{"api_version": "v1", "request_id": "req-1",
 "error": {"code": "BATCH_EMPTY", "message": "...",
           "api_status": "INVALID_INPUT", "retryable": false}}
```

- No error body contains key material, another tenant's data, transaction
  text, prompts or model output.

---

## 9. Usage reporting semantics

`GET /v1/usage` (tenant key) reports two different meters. They are never
interchangeable:

| Field | Unit | What it counts |
| --- | --- | --- |
| `used`, `monthly_limit`, `remaining` | job quota units | Units consumed by **this credential**: one per admitted API job, regardless of batch size |
| `key_quota.*` | job quota units | The same numbers, explicitly labelled (scope = this credential) |
| `tenant_usage.jobs.submitted` | jobs | Inference jobs **admitted this UTC month** |
| `tenant_usage.jobs.completed` / `failed` | jobs | Inference jobs currently in that terminal state, admitted this month |
| `tenant_usage.jobs.pending` / `retry_wait` | jobs | `QUEUED`/`PROCESSING`/`RETRY_WAIT`, admitted this month |
| `tenant_usage.jobs.document_jobs_submitted` | jobs | Document jobs admitted this month (reported separately, never merged) |
| `tenant_usage.transactions.admitted` | accounting transactions | `sum(transaction_count)` over this month's jobs; equals what the transaction quota charges |
| `tenant_usage.transactions.verified` / `review_required` / `unsupported` / `invalid_input` / `failed` | accounting transactions | **Batch rows only** currently in that state |
| `tenant_usage.transactions.blocked` / `deferred` / `pending` | accounting transactions | Admitted (charged) but never evaluated |
| `tenant_usage.transactions.tracked_rows` | rows | Per-transaction rows that exist (batch jobs only) |
| `tenant_usage.transactions.coverage` | — | Explains the difference between `admitted` and `tracked_rows` |

Accounting rules, stated exactly:

- a **batch of N transactions is 1 job and N transaction units** — never N
  jobs, never 1 transaction;
- a single-transaction job is 1 job and 1 transaction unit;
- rejected-before-admission requests (malformed, over a limit, bad key, quota
  exhausted, queue full) charge **nothing**;
- an **idempotent replay** charges nothing and creates no job;
- a **worker retry** of an already-admitted job charges nothing;
- `BLOCKED` (ordered, prior link unverified), `DEFERRED` (device budget) and
  `PENDING` were admitted and **are charged** — the unit bought admission to
  bounded work;
- **single-transaction jobs do not materialize a per-transaction row.** Their
  transaction unit is counted from the job's `transaction_count`, and their
  outcome is the job status. Per-state transaction counts therefore cover
  batch rows only and are never extrapolated;
- counters are scoped to the **current UTC calendar month by admission time**
  and are not lifetime totals;
- `GET /v1/developer/usage` (management token) is bound to the management
  tenant and reports the same counters plus both quota meters;
- `GET /v1/owner/usage` (management token) reports, per tenant, **all-time**
  job/transaction counts and **monthly** quota consumption, with both windows
  labelled (`reporting_windows`).

Limitations reported rather than hidden:

- job rows do not record which credential admitted them, so a **per-key** job
  or transaction count cannot be produced; only the quota row is per-key;
- when transaction rows exceed the jobs' admitted transaction count (which the
  admission path cannot produce), the response says so instead of adjusting
  either figure.
