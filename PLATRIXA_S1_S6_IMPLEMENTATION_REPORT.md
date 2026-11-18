# Platrixa — S1–S6 Implementation & Verification Report

**Date:** 18 November 2026  
**Repository:** `pawarpranay2010-sketch/financial-timeline-engine-v0`  
**Branch:** `preview/platrixa-v2-auth` @ `ee3475f` (working tree)  
**Deployed commit:** `b3979034` (unchanged)  
**Boundary honoured:** local repository changes and tests only. **No commit, push,
merge, deploy, migration, Railway change, credential creation, or production
inference job was performed.**

---

## 1. Stage tracker

### S1 — Worker model-readiness gate — **COMPLETE**

**Files changed**
- `scripts/platrixa_outbound_inference_worker.py`
- `scripts/platrixa_outbound_inference_worker_test.py`
- `scripts/platrixa_android_model_contract_test.py` (test harness: discovery is a GET now)
- `scripts/platrixa_worker_reliability_test.py` (test harness: fake endpoint advertises the configured model)

**Behaviour**
- Added `_get()` (the pre-S1 `preflight_model_endpoint` POSTed to `GET /v1/models`).
- `preflight_model_endpoint()` now uses `_get`.
- Added `model_ready() -> ModelReadiness` probing `GET /v1/models` with a bounded
  timeout (`DEFAULT_READINESS_TIMEOUT_S = 10`), failing closed on: unreachable,
  timeout, non-200, invalid JSON, empty list, ambiguous list under `auto`, and a
  **configured model id that the endpoint does not advertise**.
- `run_forever()` now probes readiness **before every claim**; while unready it
  claims nothing, increments `readiness_waits`, and never consumes a job attempt.
- The probe is fresh each poll, so recovery is automatic and a `MODEL_UNAVAILABLE`
  generation is followed by a probe before the next claim.
- Bridge mode is reported as `configured=False, ready=True` (the bridge owns its
  model; no documented health route exists to probe). Existing behaviour unchanged.
- `--model-id auto` still resolves at startup and, if still `auto`, is resolved by
  the readiness probe.

**Tests (11 new):** healthy endpoint; GET verb (not POST); unreachable; HTTP error;
invalid JSON; empty list; missing configured id; auto single/ambiguous; no claim
while unready; recovery; recheck after `MODEL_UNAVAILABLE`; bridge mode unchanged.

**Result:** `platrixa_outbound_inference_worker_test.py` **51/51**;
`platrixa_android_model_contract_test.py` **36/36**.

**Limitations:** no probe exists for the device-bridge path (documented, not
invented); one extra loopback GET per poll; only `/v1/models` is used (the phone
server may or may not implement `/health`, so it is not required).

---

### S2 — Termux worker persistence and lifecycle — **COMPLETE (assets + tests)**

**Files added**
- `device/termux/service/platrixa-worker/run`
- `device/termux/service/platrixa-worker/finish`
- `device/termux/boot/platrixa-worker-boot.sh`
- `device/termux/worker.env.example`
- `device/termux/install-termux-service.sh`
- `device/termux/README.md`
- `scripts/platrixa_termux_service_test.py`

**Behaviour**
- runit `run` script: PID-aware `mkdir` lock (no extra package), stale-lock
  recovery, env-file load, **key-file permission refusal (600/400)**, required
  variable checks, python/repo checks, bounded model-readiness wait
  (`READINESS_ATTEMPTS × READINESS_INTERVAL`, default 60 × 2 s), `exec` into the
  worker so runit supervises one PID, logs appended to a file.
- `finish`: bounded restart backoff (config error 60 s, other 10 s, clean 0) and
  log rotation.
- Boot script: starts `runsvdir` and `sv up platrixa-worker`, wake-lock when
  available. **It does not start the Android model app** — that app has no
  documented boot receiver.
- Installer: Termux-only guard, prerequisite check, copies assets, creates the
  env file only if absent, never reads/prints the key, does not start the service
  unless `--start` is given.
- Test-only modes: `PLATRIXA_WORKER_DRY_RUN=1`, `PLATRIXA_WORKER_PRINT_COMMAND=1`.

**Tests:** **25/25** (syntax; template; missing env/key/insecure permissions/
missing variable; model-never-ready exit 75; late-model recovery; exact argv;
no key leakage; live duplicate refused; stale lock reclaimed; finish backoff and
rotation; installer non-Termux refusal).

**Limitations:** not executed on a phone; Termux:Boot timing and runit behaviour
are unverified on-device; the model app still requires manual start after a boot.

---

### S3 — Worker observability and health reporting — **COMPLETE**

**Files changed/added**
- `scripts/platrixa_outbound_inference_worker.py`
- `scripts/platrixa_worker_observability_test.py`

**Behaviour**
- Lifecycle states: `STARTING`, `WAITING_FOR_MODEL`, `READY`, `PROCESSING`,
  `REPORTING`, `BACKING_OFF`, `STOPPED`, `FAILED`.
- `health_snapshot()`: worker id, model id, model endpoint, state + since,
  started/uptime, **liveness vs readiness**, last claim, last completed job, last
  failure category, run counters.
- Optional `--status-file` / `PLATRIXA_WORKER_STATUS_FILE`: atomic JSON rewrite
  (temp + `os.replace`) on every state change. A write failure is logged and
  never affects processing.
- `--print-status` reads the snapshot with **no** config/key/network requirement.
- No inbound listener was added (the worker's no-inbound-surface rule is intact).
- Secret/PII hygiene: no API key, model key, auth header, or transaction text in
  the snapshot (explicitly tested).

**Tests:** **34/34** (transitions, waiting-but-live, idle backoff, fatal → FAILED,
model-unavailable category, counter equality, redaction, shutdown, CLI reader).

**Limitations:** counters are per `run_forever()` run (they reset, by design);
liveness heartbeat is refreshed each poll, not by a separate watchdog thread.

---

### S4 — Multi-tenant worker claim architecture — **COMPLETE (decision + tests; no new auth model)**

**Files**
- `PLATRIXA_S4_MULTITENANT_WORKER_DECISION.md` (decision + Option B prerequisites)
- `scripts/platrixa_s4_multitenant_authorization_test.py`

**Decision:** retain **one worker identity (API key) per tenant** for the private
beta. A multi-tenant credential requires a new server-controlled assignment
primitive, result-ownership checks keyed by that credential, per-tenant fairness,
and an audit model — none exist today. Implementing it without that design would
weaken isolation, so it is documented instead (rule 11).

**Tests:** **13/13** — spoofed tenant headers are inert; A’s key claims A’s job
despite a spoofed header; no route reads a tenant header (source scan); concurrent
claims hand the raced job to exactly one worker; foreign result submission is 404;
stale generation is 409; the same Idempotency-Key under two tenants is two jobs.

---

### S5 — Queue admission and capacity protection — **COMPLETE (verified; fresh-DB fix; NOT deployed)**

**Files**
- `backend/auth/queue_capacity.py` (was untracked; re-inspected)
- `backend/auth/async_jobs.py` (local modification, pre-existing)
- `api/routes/inference_api.py`, `api/main.py` (local modifications, pre-existing)
- `backend/auth/api_keys.py` (fresh-DB fix, local modification)
- `scripts/platrixa_fresh_database_key_creation_test.py` (new regression)
- `PLATRIXA_S5_QUEUE_ADMISSION_DEPLOYMENT_CHECKLIST.md`

**Verified**
- Admission is atomic: `pg_advisory_xact_lock` → two counts → INSERT in one
  transaction; a rejection rolls back (no job, no held capacity).
- Terminal transitions release capacity in the same fenced UPDATE
  (`complete_job`, `fail_job` with retry-aware `CASE`, exhausted-lease sweep).
- Index `idx_async_jobs_capacity_held (capacity_held, tenant_id) WHERE capacity_held`.
- Errors: 503 `GLOBAL_QUEUE_FULL` / `TENANT_QUEUE_FULL`, idempotency released.
- Fresh-DB defect **confirmed present in deployed `b3979034`** (only registry DDL
  in `_ensure_schema`) and **fixed locally** (quota DDL applied first).

**Tests:** queue capacity **28/28**; capacity migration **18/18**; fresh database
**10/10**; developer workflow **10/10**; tenant isolation **14/14**.

**Limitations:** the whole capacity layer is **not deployed**; deployment needs a
deliberate commit/deploy (HEAD breaks management auth — see checklist).

---

### S6 — Real throughput and capacity benchmarking — **COMPLETE (local simulation)**

**Files**
- `scripts/platrixa_worker_benchmark.py`
- `reports/platrixa_worker_benchmark_20261118T094659Z.json`

**Measured (20 sequential jobs, simulated model latency 20 ms, local API + PostgreSQL):**

| Stage | mean | p95 | p99 |
|---|---|---|---|
| submit | 20.11 ms | 21.79 ms | 92.73 ms |
| claim | 10.67 ms | 13.11 ms | 13.36 ms |
| model (simulated 20 ms) | 22.33 ms | 22.81 ms | 22.81 ms |
| commit | 25.01 ms | 22.06 ms | 171.58 ms |
| end-to-end | 87.93 ms | 85.97 ms | 313.43 ms |
| queue wait | 10.67 ms | 13.11 ms | 13.36 ms |

- Throughput: **682 jobs/min** (simulated model).
- Readiness probe: mean 39.8 ms.
- Model unavailable: **0 claims** while unready; recovery completed the pending job.
- Queue saturation: **TENANT_QUEUE_FULL** (503).
- Two concurrent workers (distinct owner ids, one tenant): **4/4** completed,
  no duplicate completion.
- Restart during processing: lease expired → generation 1→2, commit accepted,
  recovery 0.063 s, final status VERIFIED.
- API unreachable: `TransportError` (retryable classification), no crash.
- Non-model overhead measured: **67.93 ms/job**; max RSS and CPU recorded in JSON.

**Limitations:** the model is a **stub with configured latency** — no GGUF
weights, no phone, no thermal/network effects; token counts are simulated. See §7.

---

## 2. Test evidence (exact commands and counts)

All commands ran from the repository root. Database suites ran with an embedded
disposable PostgreSQL 18 (`pgserver`) and `PYTHONPATH` pointing at the system
site-packages (pytest is not used by this repository; suites are scripts).

| # | Command | Result |
|---|---|---|
| 1 | `python scripts/platrixa_outbound_inference_worker_test.py` | **51/51**, exit 0 |
| 2 | `python scripts/platrixa_android_model_contract_test.py` | **36/36**, exit 0 |
| 3 | `python scripts/platrixa_worker_reliability_test.py` | **28/28**, exit 0 |
| 4 | `python scripts/platrixa_direct_model_validation_path_test.py` | **11/11**, exit 0 |
| 5 | `python scripts/platrixa_worker_observability_test.py` | **34/34**, exit 0 |
| 6 | `python scripts/platrixa_termux_service_test.py` | **25/25**, exit 0 |
| 7 | `python scripts/platrixa_worker_cli_flags_test.py` | OK, exit 0 |
| 8 | `python scripts/platrixa_worker_claim_outcome_logging_test.py` | **9/9**, exit 0 |
| 9 | `python scripts/platrixa_worker_fatal_report_test.py` | **7/7**, exit 0 |
| 10 | `DATABASE_URL=… python scripts/platrixa_worker_failure_report_api_test.py` | **30/30**, exit 0 |
| 11 | `… platrixa_phase1b_queue_capacity_test.py` | **28/28**, ALL PASS |
| 12 | `… platrixa_phase1b_tenant_isolation_test.py` | **14/14**, ALL PASS |
| 13 | `… platrixa_phase1b_developer_workflow_test.py` | **10/10**, ALL PASS |
| 14 | `… platrixa_phase1b_capacity_migration_test.py` | **18/18**, exit 0 |
| 15 | `… platrixa_stage2_lease_safety_integration_test.py` | **106/106**, exit 0 |
| 16 | `… platrixa_stage2_o1_lease_expiry_test.py` | **22/22**, exit 0 (23/23 on the first run) |
| 17 | `… platrixa_stage2_two_worker_race_test.py` | **23/23**, exit 0 |
| 18 | `… platrixa_s4_multitenant_authorization_test.py` | **13/13**, exit 0 |
| 19 | `… platrixa_fresh_database_key_creation_test.py` | **10/10**, exit 0 |
| 20 | `… platrixa_worker_benchmark.py --jobs 20` | report written, exit 0 |

**Tests that could not be run:** every device-side check (runit on the phone,
Termux:Boot, model-app lifecycle, screen-off soak, real GGUF inference) and any
production verification (no deployment was authorised). They are device/deploy
tasks, not skipped local checks.

**Test-harness adjustments (not weakened assertions):** two pre-S1 harnesses
assumed the model endpoint answered every call; with the readiness gate the fake
endpoints now (a) advertise the **configured** model id and (b) route
`GET /v1/models` separately. The production behaviour they assert is unchanged.

---

## 3. Architecture changes

```
Developer backend → Railway API (unchanged queue, kernel, fencing)
                         ↑ outbound HTTPS only
Termux runit service  →  worker (S1 gate + S3 state machine)
    │  run waits for GET /v1/models, then execs the worker
    │  worker probes GET /v1/models BEFORE EVERY CLAIM
    ↓
phone llama.cpp server on 127.0.0.1:11434 (unchanged, loopback only)
```

- **Readiness is now a precondition of claiming**, not a reaction to failure.
- **Persistence** is a reproducible runit service with bounded restarts and a
  duplicate guard; Termux:Boot only starts the supervisor.
- **Observability** is a local JSON status file plus `--print-status`; no new
  network surface.
- **Tenant authorization** is unchanged: one key = one tenant = one worker scope
  (S4 decision).
- **Admission** (`queue_capacity`) is atomic, durable, bounded and released on
  terminal states; it is ready to deploy but not deployed.
- **Benchmarking** is a local, reproducible harness with an explicit
  simulated-vs-measured split.

---

## 4. Security review

| Property | Status |
|---|---|
| Authentication | Unchanged: `X-Platrixa-API-Key`, server-derived tenant |
| Tenant isolation | Intact: S4 13/13, isolation 14/14, spoofed tenant headers inert |
| Idempotency | Intact: tenant-scoped, replay returns the original job, no extra quota |
| Quota enforcement | Intact: per key; capacity rejection releases the idempotency claim |
| Lease ownership | Intact: owner + generation required on lease/result/failure |
| Fencing | Intact: stale generation 409, nothing stored (S4 C2, lease safety 106/106) |
| Secret redaction | Worker redacts API/bridge/model keys; status file contains none (tested) |
| Key file hygiene | runit service refuses a key file that is not 600/400 |
| No inbound surface | Preserved: worker and service make outbound calls only |
| Deterministic authority | Unchanged: model output remains a candidate; `VERIFIED` still clamped server-side |
| No production action | No deploy, migration, credential, or production job was performed |

---

## 5. Android activation instructions (device-side, not executed here)

Inside Termux, from the repository clone:

```sh
pkg update
pkg install python curl runit termux-api
# Install Termux:Boot from F-Droid and open it once.

mkdir -p "$HOME/.platrixa"
# Paste the tenant key when prompted (not echoed):
read -r -s -p "tenant API key: " KEY; printf '%s' "$KEY" > "$HOME/.platrixa/worker.key"; unset KEY
chmod 600 "$HOME/.platrixa/worker.key"

cp device/termux/worker.env.example "$HOME/.platrixa/worker.env"
chmod 600 "$HOME/.platrixa/worker.env"
nano "$HOME/.platrixa/worker.env"          # set PLATRIXA_REPO_DIR to this clone

sh device/termux/install-termux-service.sh  # prepares; does not start

curl -s http://127.0.0.1:11434/v1/models    # must list PLATRIXA_WORKER_MODEL_ID

sv up platrixa-worker                       # start
sv status platrixa-worker
tail -f "$HOME/.platrixa/logs/worker.log"
```

The **Android model app must be started manually after a reboot** — no boot
receiver is documented. The service waits for the endpoint and the worker gates
claims, so a late model is reported as not-ready, never as failed jobs.

Recovery commands (`sv restart`, log paths, uninstall) are in
`device/termux/README.md`.

---

## 6. Production deployment checklist (not performed)

**NO PRODUCTION DEPLOYMENT WAS PERFORMED.**

1. **Branch/commit:** do not promote `ee3475f` as-is — it removes static-token
   management auth and production lacks the assertion secret. Create a release
   branch from `b3979034` and cherry-pick/commit the S5 changes only.
2. **Commit the untracked capacity module** (`backend/auth/queue_capacity.py`) —
   without it `api.main` raises `ModuleNotFoundError` at startup.
3. **Migrations:** all idempotent; `capacity_held` + partial index auto-applied
   by `async_jobs._ensure_schema()`; quota/registry/visitor DDL already applied.
4. **Env:** `PLATRIXA_QUEUE_GLOBAL_LIMIT` (default 100),
   `PLATRIXA_QUEUE_PER_TENANT_LIMIT` (default 10, must be ≤ global). Startup
   fails closed on invalid values.
5. **Verify:** readiness, 401 on unauthenticated claim, `reconcile()` divergence
   counts zero, one saturation check on a scratch tenant.
6. **Rollback:** raise the limits (soft) or redeploy `b3979034` (column is
   additive); no destructive migration.
7. **Do not deploy the worker changes as a service** until the device commands in
   §5 are run with authorisation; worker code is backward compatible with the
   deployed API (readiness is an extra loopback GET).

---

## 7. Capacity conclusion

**What was actually measured (local, simulated model):** readiness ~40 ms;
claim ~10.7 ms mean; commit ~25 ms mean; non-model overhead **67.9 ms/job**;
queue wait ~10.7 ms; 682 jobs/min against a 20 ms stub; zero claims while the
model was unready; saturation returns `TENANT_QUEUE_FULL`; restart recovery in
63 ms with generation advancement.

**What remains unproven:** real device inference time, token throughput, thermal
behaviour, memory ceiling, network latency to Railway, burst behaviour with the
per-tenant pending bound of 10, and multi-worker behaviour across tenants.

**Defensible statement:** monthly VOLUME is not the binding constraint at
plausible device speeds (45 s/job ⇒ ~17.4% duty for 100 developers × 100
jobs/month), but **a single worker processes one job at a time**, the per-tenant
queue is bounded at 10, and device throughput has not been measured. Therefore
Platrixa must **not** advertise 100-developer support on one worker; the honest
position is one worker identity per tenant for the beta (S4), with capacity claims
deferred until the benchmark is run against a real GGUF model on the phone.

*End of report.*
