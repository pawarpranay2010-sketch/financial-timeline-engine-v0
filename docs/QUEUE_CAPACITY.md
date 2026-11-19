# Pending-queue capacity (Phase 1B)

The developer API bounds how many jobs may be **pending** at once. This is a
queue-protection bound, separate from the **monthly quota** (`TenantQuota`,
`backend/auth/gate.py`): saturating the queue never changes, refunds, or borrows
monthly usage.

Module: `backend/auth/queue_capacity.py`.

## Source of truth

Durable pending capacity is **derived from the job table**, not from process
memory:

```sql
-- pending jobs
SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE;
-- pending jobs for one tenant
SELECT count(*) FROM platrixa_async_jobs WHERE capacity_held = TRUE AND tenant_id = :t;
```

`capacity_held` is `TRUE` from the row's INSERT until the job reaches a terminal
state. There is no counter table, so there is nothing to drift and nothing to
lose on a restart.

## What consumes a slot

| Job state | Slot held? | Transition |
|---|---|---|
| `QUEUED` | yes | INSERT (inside the admission transaction) |
| `PROCESSING` (claimed) | yes | claim (`QUEUED → PROCESSING`) does **not** release |
| `RETRY_WAIT` (backoff) | yes | retryable failure inside the retry budget |
| `COMPLETED` | no | terminal UPDATE sets `capacity_held = FALSE` |
| `FAILED` (permanent, or retry budget exhausted) | no | terminal UPDATE sets `capacity_held = FALSE` |
| lease expired, still `PROCESSING` | yes | released at reclaim/terminalisation |
| worker crash / restart | yes | recovery preserves accounting |
| cancellation | not supported by the API | if added it must clear the same flag |

Releases happen in the **same atomic, fenced UPDATE** that writes the terminal
state and require `status='PROCESSING'`, so a slot is released **exactly once**;
a duplicate completion attempt changes nothing.

## Admission atomicity

`POST /v1/inference-jobs` and `POST /v1/documents` do not pre-check capacity.
Admission happens inside `async_jobs.create_job` in one transaction:

1. `pg_advisory_xact_lock(1347246673)` — cluster-wide, transaction-scoped.
2. count pending globally and for the tenant;
3. raise `QueueCapacityExceeded` (rolled back) if either limit is reached;
4. `INSERT ... capacity_held = TRUE`;
5. `COMMIT`.

The advisory lock serialises only the brief admission window across every
Railway instance and every restarted process, so two concurrent requests can
never consume the same remaining slot. A rejection rolls back **and** the caller
releases the idempotency claim, so no orphaned job, reservation, or stranded
`Idempotency-Key` remains.

HTTP mapping: **503** with `GLOBAL_QUEUE_FULL` or `TENANT_QUEUE_FULL`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PLATRIXA_QUEUE_GLOBAL_LIMIT` | `100` | maximum pending jobs, cluster-wide |
| `PLATRIXA_QUEUE_PER_TENANT_LIMIT` | `10` | maximum pending jobs per tenant |

Defaults are **conservative engineering values** for the current deployment (a
single Android/Termux worker claims one inference job at a time), not measured
throughput. A single worker cannot drain a large backlog, so a small bound is the
honest one.

Validation is strict and runs at startup (`api.main.create_app` →
`queue_capacity.validate_config`):

* a non-integer, zero, or negative value raises `QueueLimitConfigurationError`
  and the app refuses to start — protection is never silently disabled;
* a per-tenant limit larger than the global limit is also rejected.

To change the limits: set the two variables in the deployment environment and
redeploy. No code or schema change is needed.

## Reconciliation

```python
from backend.auth import queue_capacity

queue_capacity.reconcile()
# {"pending_rows": N, "terminal_still_held": 0, "pending_already_released": 0,
#  "consistent": True}

queue_capacity.repair()   # explicit, idempotent repair of both divergence classes
```

Divergence classes detected: a terminal row that still holds a slot
(`terminal_still_held`) and a pending row that holds none
(`pending_already_released`). Nothing is repaired implicitly.

## Tests

```bash
python3 scripts/platrixa_phase1b_queue_capacity_test.py
python3 scripts/platrixa_phase1b_tenant_isolation_test.py
python3 scripts/platrixa_phase1b_developer_workflow_test.py
```

All three start their own disposable embedded PostgreSQL (pgserver) and refuse
any database URI that is not a local `/tmp` socket.

## Owner report

```bash
PLATRIXA_METERING_DATABASE_URL=... python3 scripts/platrixa_owner_report.py
```

Read-only (the session runs `SET TRANSACTION READ ONLY`): tenants, labels,
masked key prefixes, lifecycle status, job/status counts, pending workload,
quota usage. It never reads transaction text, prompts, candidate payloads, key
material, or management credentials.
