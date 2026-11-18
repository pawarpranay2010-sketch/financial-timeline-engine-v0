# Platrixa S5 — queue admission & fresh-database fix: deployment checklist

**NO PRODUCTION DEPLOYMENT WAS PERFORMED.** This is the prepared checklist only.

## 1. What exists locally (and is NOT deployed)

| File | State | Note |
|---|---|---|
| `backend/auth/queue_capacity.py` | **untracked** | Global 100 / per-tenant 10, advisory-lock admission, reconciliation, repair |
| `backend/auth/async_jobs.py` | modified | `capacity_held` column + migration, atomic admission in `create_job`, release on terminal states, retry-aware release in `fail_job`, exhausted-lease release |
| `api/routes/inference_api.py` | modified | maps `QueueCapacityExceeded` to 503 with `GLOBAL_QUEUE_FULL` / `TENANT_QUEUE_FULL` and releases the idempotency claim |
| `api/main.py` | modified | `queue_capacity.validate_config()` at startup (fail-fast on bad limits) |
| `backend/auth/api_keys.py` | modified | `_ensure_schema` now applies quota DDL BEFORE the registry DDL (fresh-DB defect) |

Deployed commit `b3979034` contains **none** of these (`git show b3979034:api/routes/inference_api.py | grep queue_capacity` → empty;
`git show b3979034:backend/auth/api_keys.py` `_ensure_schema` applies only the registry DDL).
Deploying the current HEAD would additionally break management auth (assertion-only
auth without the production assertion secret), so this work must be committed and
deployed deliberately, not by promoting HEAD.

## 2. Migrations (all idempotent, no Alembic in this repository)

1. `backend/database/platrixa_tenant_quota_schema.sql` — already applied in
   production (the quota table exists). Re-running is safe.
2. `backend/database/platrixa_api_keys_schema.sql` — already applied.
3. `capacity_held` migration — shipped inside `async_jobs._ensure_schema()`
   (`ADD COLUMN IF NOT EXISTS ... DEFAULT TRUE`, backfill terminal rows to FALSE,
   partial index `idx_async_jobs_capacity_held (capacity_held, tenant_id) WHERE capacity_held`).
   It runs automatically on the first async-store use after deploy.
4. `backend/database/platrixa_visitor_schema.sql` — required by `create_job`
   (visitor ownership columns); already applied in production.

Verify after deploy (read-only SQL):

```sql
\d+ platrixa_async_jobs             -- capacity_held present
SELECT count(*) FROM platrixa_async_jobs
 WHERE status IN ('COMPLETED','FAILED') AND capacity_held = TRUE;   -- must be 0
SELECT count(*) FROM platrixa_async_jobs
 WHERE status IN ('QUEUED','PROCESSING','RETRY_WAIT') AND capacity_held = FALSE;  -- must be 0
SELECT * FROM pg_indexes WHERE indexname = 'idx_async_jobs_capacity_held';
```

`queue_capacity.reconcile()` reports both divergence classes; `repair()` is the
explicit operator fix (never automatic).

## 3. Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PLATRIXA_QUEUE_GLOBAL_LIMIT` | 100 | cluster-wide pending jobs |
| `PLATRIXA_QUEUE_PER_TENANT_LIMIT` | 10 | pending jobs per tenant (must be ≤ global) |

Startup FAILS if either is not a positive integer, or if per-tenant > global.
Recommended rollback lever: raise both limits (e.g. 10000) and redeploy; the
schema and code stay in place, admission simply stops binding.

## 4. Pre-deploy verification (local, already green)

| Suite | Result |
|---|---|
| `scripts/platrixa_phase1b_queue_capacity_test.py` | 28/28 |
| `scripts/platrixa_phase1b_developer_workflow_test.py` | 10/10 |
| `scripts/platrixa_phase1b_tenant_isolation_test.py` | 14/14 |
| `scripts/platrixa_phase1b_capacity_migration_test.py` | 18/18 |
| `scripts/platrixa_fresh_database_key_creation_test.py` | 10/10 |

## 5. Rollback plan

1. **Soft**: set `PLATRIXA_QUEUE_GLOBAL_LIMIT`/`PLATRIXA_QUEUE_PER_TENANT_LIMIT`
   high and redeploy (admission no longer refuses).
2. **Code**: redeploy `b3979034` (the last known production commit). The
   `capacity_held` column is additive (default TRUE); an older app ignores it,
   and the partial index is harmless.
3. **Data**: no destructive migration is performed. If the flag ever diverges,
   `queue_capacity.reconcile()` reports it and `repair()` corrects it.

## 6. Post-deploy checks (read-only)

```bash
curl -s "$BASE/v1/ready"                       # admission.quota_enforced true
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$BASE/v1/worker/jobs/claim"  # 401 without a key
# Submit (per-tenant limit + 1) jobs with ONE beta key → the last is 503 with
# TENANT_QUEUE_FULL and Retry-After semantics preserved.
```

Do not run the saturation check against a tenant that has real pending work.
