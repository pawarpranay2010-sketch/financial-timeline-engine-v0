# Platrixa — Worker Reliability Repair Report

**Date:** 2026-11-09
**Scope:** the three confirmed worker reliability defects — failure reporting / job recovery, HTTP 429 + `Retry-After`, provider/model provenance.
**Architecture preserved:** Termux → Android llama.cpp loopback → Railway API → deterministic kernel. No external provider, no tunnel, no hosting change.

### Repair status

| # | Repair | Status |
|---|---|---|
| 1 | Failure reporting and job recovery | **FIXED** (requires an API deploy — see §9) |
| 2 | HTTP 429 handling and `Retry-After` | **FIXED** |
| 3 | Provider and model provenance | **FIXED** |

All three are covered by executed tests (§7, §8). The only caveat is deployment ordering: the new failure route must be deployed before the new worker can use it, and until then the worker degrades safely to the old lease-expiry recovery (§4.3).

---

## 1. Initial repository state

Recorded before any edit.

| Fact | Value |
|---|---|
| Repository | `https://github.com/pawarpranay2010-sketch/financial-timeline-engine-v0.git` |
| Working directory | `/workspace/chats/66c7e92532ed263259fbf566` (a separate clone — **not** your Termux tree) |
| Branch | `platrixa/stage2-device-benchmark-review` |
| HEAD | `778a7e174432add1d4431341f9f82bbea54de266` |
| Staged changes | none |
| Pre-existing modified (untouched by this sprint) | `reports/local_bridge_acceptance.json`, `scripts/platrixa_stage2_o1_lease_expiry_test.py` |
| Pre-existing untracked (untouched) | `api/tracing.py`, `frontend/web/*`, `recovery_patches/`, `tmp/`, `package-lock.json`, `platrixa_direct_model_recovery.patch`, `scripts/platrixa_control_plane_reader.py`, `scripts/platrixa_ticker_verify.py`, `tests/test_platrixa_ticker_verify.py`, `.frontend-web-playwright-env.env`, `RUNBOOK_PLAYWRIGHT_VERIFICATION.md`, plus the two earlier audit reports |

No conflict was found in any file this sprint needed: the worker, both its test suites, `api/routes/inference_api.py` and `backend/auth/async_jobs.py` were all clean at HEAD. Nothing was reset, stashed, cleaned, restored, or discarded. HEAD is still `778a7e1` after the work, and no commit, push, merge or branch change was made.

**One deliberate edit to an existing test file:** `scripts/platrixa_worker_claim_outcome_logging_test.py`. Its fake API handler had a strict "unexpected API call" allowlist that rejected the new `/failure` request. The handler now serves the route's documented 200 body, and the bridge-failure test gained two assertions (`failures_reported == 1`, `failure_report_failures == 0`). No assertion was weakened — the change makes the fake model the real contract and tightens the test.

---

## 2. Root cause of each defect

### 2.1 Defect 1 — failures reported nothing, so jobs stranded in `PROCESSING`

Two independent faults had to line up, and they did.

**Fault A — there was no failure-reporting path at all.** `run_forever` caught `BridgeUnavailableError` / `BridgeResponseError`, logged a line, incremented `bridge_failures`, and `continue`d. Nothing told the server. The job's only exit from `PROCESSING` was lease expiry:

```python
except BridgeUnavailableError as exc:
    report["bridge_failures"] += 1
    logger.warning("job %s: local bridge unavailable (%s)", job.job_id, exc)
    continue
```

**Fault B — and once the budget was spent, expiry did not recover it either.** `claim_next_job` requires `attempt_count < max_attempts` (`backend/auth/async_jobs.py`, claim predicate), and `attempt_count` increments on every claim. `fail_job` was only ever called from the server's own `_fail_inference_job` (unreadable stored payload) or from the result route when the kernel returned `MODEL_UNAVAILABLE`. No code path terminalised a job that had simply been abandoned.

Composed, the state machine had an unreachable terminal state:

```
claim #3 (attempt_count = 3 = max_attempts)
   └─ model call fails → nothing reported → lease lapses
        ├─ claimable?  NO  (attempt_count < max_attempts is false)
        └─ terminal?   NO  (nothing writes FAILED)
             ⇒ PROCESSING forever. The job is silently lost, and the queue
               looks merely idle (NO_WORK) to every subsequent claim.
```

That is the exact defect the sprint called "failed jobs remain indefinitely in PROCESSING" — and its cause was structural, not a missing log line.

### 2.2 Defect 2 — 429 was indistinguishable from a dead model

`call_model` collapsed every non-200 into one exception:

```python
status = int(response.status_code)
if status != 200:
    raise BridgeUnavailableError(f"model endpoint returned HTTP {status}: ...")
```

Consequences: no `Retry-After` parsing; no distinction between "slow down" (429), "your key is wrong" (401/403), "your request is wrong" (400/422) and "the server is broken" (5xx); no waiting; and — because of 2.1 — every retry attempt consumed a fresh claim from the job's 3-attempt budget. A provider that throttles for 30 seconds could therefore burn a job's entire budget in three claims without ever attempting the actual work.

The Android llama.cpp server does not normally rate-limit, but the defect is real for (a) the app's request pacing under concurrent load, (b) any proxy in front of it, and (c) any future endpoint — and it was the difference between "retryable" and "lost job".

### 2.3 Defect 3 — provider identity was a hardcoded literal

```python
"model": {
    "provider": "android-llama-server",   # ← always, regardless of config
    ...
}
```

The server reads this block via `device_reported_model_identity()` into evidence. So the evidence chain asserted a provider that the worker had never verified, and a different configured backend would still be recorded as `android-llama-server`. That is a provenance claim the worker was not entitled to make.

---

## 3. Files changed and exact changes

| File | Change |
|---|---|
| `scripts/platrixa_outbound_inference_worker.py` | +578/−37 lines: failure reporting, 429/`Retry-After`, provenance, CLI |
| `api/routes/inference_api.py` | +239 lines: `POST /v1/worker/jobs/{job_id}/failure`, claim-path sweep call |
| `backend/auth/async_jobs.py` | +76 lines: `fail_exhausted_leases()` + `LEASE_EXHAUSTED_CODE` |
| `scripts/platrixa_worker_claim_outcome_logging_test.py` | +10 lines: fake serves `/failure`; bridge-failure test asserts the report |
| `scripts/platrixa_worker_reliability_test.py` | **new** — 28 offline checks |
| `scripts/platrixa_worker_failure_report_api_test.py` | **new** — 30 checks against real PostgreSQL |

### 3.1 Worker — new constants

`FAILURE_SUFFIX = "/failure"`, `STATUS_RETRY_WAIT`, `STATUS_FAILED`, `FAILURE_OUTCOMES`, the five reportable codes (`MODEL_UNAVAILABLE`, `VALIDATION_FAILED`, `INTERNAL_ERROR`, `UNAUTHORIZED`, `REQUEST_MALFORMED`), `HTTP_429_RATE_LIMITED`, `HTTP_AUTH_REJECTED`, `HTTP_REQUEST_REFUSED`, `DEFAULT_RATE_LIMIT_FALLBACK_S`, `RETRY_AFTER_MAX_HEADER_S`, `DEFAULT_MODEL_PROVIDER`, `PROVENANCE_UNKNOWN`, `UNKNOWN_PROVIDER_ALIASES`, `MODEL_PROVIDER_ENV`, `RATE_LIMIT_MAX_RETRIES_ENV`, `RATE_LIMIT_MAX_WAIT_ENV`. The lease-proof path regex now accepts the `failure` suffix.

### 3.2 Worker — new/public functions and methods

| Symbol | Purpose |
|---|---|
| `parse_retry_after(value, *, now=None)` | Both RFC 9110 forms → seconds, or `None` |
| `classify_processing_failure(exc)` | `(server error code, bounded reason)` — fixed taxonomy, never exception text |
| `bounded_failure_message(reason)` | single line, ≤200 chars |
| `ModelRateLimitError(BridgeUnavailableError)` | 429 budget/window exhausted; carries `retry_after_s`, `attempts` |
| `ModelRequestRejected(BridgeUnavailableError)` | endpoint refused the request; carries `code`, `status_code` |
| `worker.failure_url(job)` | derived fenced URL (not trusted from the claim) |
| `worker.submit_failure(job, code, reason)` | the fenced failure report |
| `worker._report_processing_failure(job, exc, report)` | classify → report → account; never raises |
| `worker._resolve_model_provider(config)` | provenance from configuration only |
| `worker._retry_after_header`, `_rate_limit_wait_s`, `_sleep_within_lease` | 429 policy |

### 3.3 Worker — behavioural edits

* `WorkerConfig` gained `model_provider`, `rate_limit_max_retries` (default 2), `rate_limit_max_wait_s` (default 60), `rate_limit_fallback_s` (default 5). Appended last, so every existing keyword construction is unaffected.
* `__init__` validates the retry count (0–10), the wait ceiling (>0) and the fallback (≥0), resolves provenance, and holds `_active_lease`.
* `process_job` sets `_active_lease = job.state` for the duration of the model call and clears it in `finally`.
* `call_model` is now a bounded request loop: 429 → parse → clamp → lease-safe wait → re-issue; 401/403 → `ModelRequestRejected(UNAUTHORIZED)`; 400/404/422 → `ModelRequestRejected(REQUEST_MALFORMED)`; other non-200 → `BridgeUnavailableError` (unchanged).
* `call_model`'s `model.provider` now comes from `self._model_provider`; `artifact_identity` stays `PROVENANCE_UNKNOWN`.
* `run_forever` merges the two bridge exception branches and now **reports** before continuing. Counters `failures_reported` and `failure_report_failures` were added to `RUN_REPORT_KEYS` and `format_run_report`.

### 3.4 Checked-in invariants that did NOT change

`normalize_model_url` (loopback-only, remote hosts still refused), the Alpaca prompt and its byte-equality guard, `max_tokens`, temperature/top_p, the 64 KiB response cap, the 120 s model timeout, fail-closed parsing, the 64 KiB submission cap, the heartbeat's separate per-job stop event, `LeaseState` semantics, result `409 → LeaseLost`, and every exit code.

---

## 4. Failure and recovery state transitions

### 4.1 Classification (worker → server)

| Worker condition | Reported code | Server decision | Next state |
|---|---|---|---|
| 429, retry budget spent or lease window too short | `MODEL_UNAVAILABLE` | retryable | `RETRY_WAIT` (or `FAILED` if budget spent) |
| Endpoint unreachable / timeout / 5xx | `MODEL_UNAVAILABLE` | retryable | `RETRY_WAIT` / `FAILED` |
| Device bridge unavailable (503 / connection) | `MODEL_UNAVAILABLE` | retryable | `RETRY_WAIT` / `FAILED` |
| Malformed / empty / incomplete-stream output | `VALIDATION_FAILED` | permanent | `FAILED` |
| 401 / 403 from the model endpoint | `UNAUTHORIZED` | permanent | `FAILED` |
| 400 / 404 / 422 from the model endpoint | `REQUEST_MALFORMED` | permanent | `FAILED` |
| Unexpected worker exception | `INTERNAL_ERROR` | retryable | `RETRY_WAIT` / `FAILED` |
| Candidate produced, server rejects it (422) | *no report* — the server stores its own terminal envelope | — | `COMPLETED` (refused) |
| Submission transport failed / 5xx | *no report* — outcome is UNKNOWN, never asserted | — | lease expiry, then reclaim |

Retryability is **never** sent by the worker: the server calls `classify_failure(error_code, api_status)` on its own vocabulary. The worker can only choose from five existing codes.

### 4.2 Job lifecycle after the repair

```
            ┌──────────── claim (attempt_count += 1, generation += 1) ────────────┐
            ▼                                                                     │
        PROCESSING ──result 200──▶ COMPLETED (terminal, authoritative)             │
            │                                                                     │
            ├──worker reports MODEL_UNAVAILABLE / INTERNAL_ERROR──────────────────┘
            │     budget left → RETRY_WAIT (next_attempt_at = now + backoff)
            │     budget spent → FAILED (terminal)
            │
            ├──worker reports VALIDATION_FAILED / UNAUTHORIZED / REQUEST_MALFORMED
            │     → FAILED (terminal, immediately)
            │
            ├──worker vanishes (crash, kill, power loss) → lease lapses
            │     budget left → claimable again (unchanged crash recovery)
            │     budget spent → SWEEP → FAILED(LEASE_EXHAUSTED)   ← the new exit
            │
            └──report refused (409) → worker marks the lease lost, writes nothing
```

The sweep is the missing edge in that diagram. Before it, the second-to-last row was a dead end.

### 4.3 Backwards/deployment compatibility

| Combination | Behaviour |
|---|---|
| New worker + new API (deployed) | Full repair: failures move to `RETRY_WAIT`/`FAILED` |
| New worker + **old** API | `/failure` → 404 → counted as `failure_report_failures`, logged, **no crash**; recovery falls back to lease expiry + the sweep's absence (i.e. the old behaviour) |
| Old worker + new API | Unchanged; it simply never calls the new route; the sweep still terminalises its abandoned jobs |

Nothing in the worker requires the server change to keep running, which is why the deploy ordering is safe in either direction.

---

## 5. `Retry-After` handling behaviour

**Parsing** (`parse_retry_after`):

* `delay-seconds` — non-negative integer; a float (`1.5`) is tolerated because proxies emit it.
* `HTTP-date` — via `email.utils.parsedate_to_datetime`; a zone-less date is treated as GMT; the delta is taken from wall clock (or an injected `now`).
* Absent / empty / unparseable (`"soon"`, `"next tuesday"`) → `None`, meaning "no usable hint", **not** zero.
* Negative → `None`; NaN/inf → `None`; a past date → `0.0`.
* Anything above one day is capped at 86 400 s as not credible.

**Policy** (`_rate_limit_wait_s`):

1. Use the header when usable.
2. Otherwise fall back to `rate_limit_fallback_s × 2^(attempt-1)` (5 s, 10 s, …).
3. Clamp the result to `[0, rate_limit_max_wait_s]` (default 60 s) — a `Retry-After: 3600` cannot park a leased job for an hour.
4. Re-issue the same request; at most `rate_limit_max_retries` extra attempts (default 2 → 3 requests).

**Lease safety** (`_sleep_within_lease`):

* The wait is allowed only if it fits inside `deadline − now − max(1, lease_seconds/4)` using the **confirmed** deadline. A renewal that has not happened is never assumed.
* If it does not fit → no sleep at all, `ModelRateLimitError` → reported as `MODEL_UNAVAILABLE` (retryable), lease released by the server.
* The wait is sliced into 0.25 s steps, so a stop request or a lost lease is noticed immediately; a lost lease raises `LeaseLost` and nothing is submitted.
* Slices are **counted**, not measured against the clock, so an injected test clock cannot spin.

**Distinctions** (the reason this is not one big "retry" bucket): 429 waits and retries; 401/403 is terminal `UNAUTHORIZED`; 400/404/422 is terminal `REQUEST_MALFORMED`; 5xx is retryable `MODEL_UNAVAILABLE`; a malformed candidate is terminal `VALIDATION_FAILED`.

**Configuration:** `--rate-limit-max-retries` / `PLATRIXA_WORKER_RATE_LIMIT_MAX_RETRIES`, `--rate-limit-max-wait` / `PLATRIXA_WORKER_RATE_LIMIT_MAX_WAIT_S`. Diagnostics log the delay, the attempt number and the bound — never a header value that could contain a credential, and never a payload.

---

## 6. Provenance rules

1. **Configuration only.** `provider` is resolved once in `__init__`, before any request, and the response body's `model` field is never read. A model server cannot name the provider that served it.
2. **Explicit override wins:** `--model-provider` / `PLATRIXA_WORKER_MODEL_PROVIDER` (bounded `^[A-Za-z0-9._-]{1,64}$`).
3. **`unknown` resolves to explicit unknown:** the aliases `unknown`, `provenance_unknown`, `unset` → `PROVENANCE_UNKNOWN`. Unknown provenance stays visibly unknown; it is never replaced by a plausible name.
4. **Default is truthful, not arbitrary:** with no override and a configured model endpoint, the backend *is* the phone's llama.cpp server, so `android-llama-server` is recorded — byte-identical to the previous behaviour, which is why existing device records and the existing `platrixa_direct_model_validation_path_test.py` assertion still hold.
5. **Bridge mode records nothing:** when the device bridge supplies the body, the worker passes it through verbatim and constructs no model block, so it cannot overwrite the bridge app's own identity.
6. **Invalid configuration fails fast:** a provider string outside the bounded pattern is a `WorkerConfigError` (exit 2), not a silent sanitisation into a false claim.
7. **Model identity** is the configured `model_id`, never a value echoed by the endpoint.
8. **`artifact_identity` remains `PROVENANCE_UNKNOWN`.** No SHA-256 of the GGUF exists anywhere in this repository, so the worker has no basis to assert one. The repair removes a false *provider* claim; it does not invent an artifact identity.
9. **Provenance is not authority.** It rides in the untrusted `bridge_body`; the server still labels it device-reported and unverified, and it cannot influence `VERIFIED`.

---

## 7. Tests added and executed

### 7.1 New: `scripts/platrixa_worker_reliability_test.py` (28 checks, offline)

*Retry-After parsing:* delay-seconds; HTTP-date; absent/invalid → `None`; past date → `0.0`; absurd value → credibility ceiling.

*429 end-to-end:* 429 + delay-seconds then recovery (2 requests, delay honoured, result submitted, **no** failure reported); 429 + HTTP-date then recovery; 429 without the header (bounded fallback); 429 with an invalid header (fallback, not zero); `Retry-After: 3600` clamped to the configured 8 s; a 30 s wait against a 20 s lease refused with **zero** sleep; budget exhausted → `MODEL_UNAVAILABLE` reported.

*Classification:* 401 → `UNAUTHORIZED`, one request only, no wait; 422 → `REQUEST_MALFORMED`, one request only; 503 → `MODEL_UNAVAILABLE`; malformed output → `VALIDATION_FAILED` with **no** `/result` call.

*Failure-report contract:* payload is exactly `{lease, error}` with the right owner/generation; no raw input, no API key, no bridge key, message ≤200; a 409 is counted as `lease_lost` and never raises; a 503 on the report is counted as `failure_report_failures` and never raises; a 200 with an unknown `status` is **not** counted as reported.

*Provenance:* default `android-llama-server`; explicit override; `unknown` → `PROVENANCE_UNKNOWN`; invalid config refused (3 shapes); a lying response (`model: gpt-9-omniscient`, `provider: openai`) cannot alter the block; bridge mode transports the body verbatim.

### 7.2 New: `scripts/platrixa_worker_failure_report_api_test.py` (30 checks, real PostgreSQL + real FastAPI app)

A retryable failure → 200 `RETRY_WAIT`, row `RETRY_WAIT`, lease released, `result_json` NULL, code stored. A permanent failure → `FAILED`. An unsupported code → 400 and **no write**. A superseded generation → 409 and the newer worker's lease/status untouched. A completed job → 409, status still `COMPLETED`, stored result byte-identical. A foreign tenant → 404, nothing written. The budget walks `RETRY_WAIT → RETRY_WAIT → FAILED` through the report path with no result ever written. The sweep terminalises a job that spent its budget and lost its lease, writing `LEASE_EXHAUSTED` and no result.

### 7.3 Existing suites re-run (no assertion weakened)

| Suite | Result |
|---|---|
| `platrixa_outbound_inference_worker_test.py` | 40/40 |
| `platrixa_worker_cli_flags_test.py` | all pass (4) |
| `platrixa_worker_fatal_report_test.py` | 7/7 |
| `platrixa_worker_claim_outcome_logging_test.py` | 9/9 |
| `platrixa_direct_model_validation_path_test.py` | 11/11 |
| `fte_fyjc_89_outbound_inference_bridge_test.py` | **116/116** |
| `platrixa_stage2_lease_safety_integration_test.py` | **106/106** |
| `platrixa_worker_reliability_test.py` (new) | 28/28 |
| `platrixa_worker_failure_report_api_test.py` (new) | 30/30 |

`fte_fyjc_89` and the lease-safety suite are the important regressions here: they exercise the claim route my sweep modifies and the fenced commit path, and both are fully green.

### 7.4 CLI verification (manual, loopback only)

```
python3 scripts/platrixa_outbound_inference_worker.py --api-base-url http://127.0.0.1:9 \
  --api-key test-key --worker-id w-1 --model-url http://127.0.0.1:11434 \
  --model-provider phone-llamacpp --rate-limit-max-retries 1 --rate-limit-max-wait 5 --once
→ run report: claims=1 … failures-reported=0 failure-report-failures=0

… --model-provider "bad provider!"
→ worker configuration error: model provider must match ^[A-Za-z0-9._-]{1,64}$   (exit 2)
```

Compilation: `python3 -m py_compile` on all six changed files → clean.

---

## 8. Test results, limitations and what was NOT run

**No remaining failures in anything executed.** Everything above ran to completion with the exit status shown.

Not executable in this environment, with the reason:

| Not run | Why |
|---|---|
| `pytest` collection | `pytest` is not installed here; every suite is also a self-running script, and all of them were executed directly |
| `platrixa_stage2_o1_lease_expiry_test.py` | Targets a **live** API base URL (`--api-base-url`) and is one of the pre-existing user-modified files; running it would mean a production claim, which the sprint forbids |
| `platrixa_stage2_o6_state_and_crash_test.py` | Requires a live API base plus process-level `SIGKILL` choreography |
| `platrixa_stage2_two_worker_race_test.py` | Requires a live API base |
| `platrixa_worker_failure_report_api_test.py` under the plain system Python | Needs the API dependencies; it was run in a throwaway venv (see below) |

The API-dependent suites were run in a temporary virtualenv created for this sprint and then **deleted** (`.verify-venv`, removed — no trace in the tree). To reproduce:

```
python3 -m venv .venv && .venv/bin/pip install fastapi httpx SQLAlchemy psycopg2-binary pgserver \
    pydantic pydantic-settings requests python-multipart uvicorn
.venv/bin/python scripts/platrixa_worker_failure_report_api_test.py
.venv/bin/python scripts/fte_fyjc_89_outbound_inference_bridge_test.py
```

Embedded PostgreSQL data lives under `/tmp/platrixa_fte89_pgdata` and is discarded; no external database was touched.

---

## 9. API changes required (implemented, additive)

Both are new surface, not modifications of existing behaviour.

### 9.1 `POST /v1/worker/jobs/{job_id}/failure`

```
Headers: X-Platrixa-API-Key (tenant)
Body:    {"lease": {"owner": "...", "generation": N},
          "error": {"code": "MODEL_UNAVAILABLE", "message": "<=200 chars"}}

200  {"api_version", "job_id", "status": "RETRY_WAIT"|"FAILED",
      "retryable": bool, "attempts": N, "max_attempts": M}
400  REQUEST_MALFORMED   (missing lease proof, or an unsupported code)
404  JOB_NOT_FOUND       (foreign tenant / wrong kind / nonexistent — identical)
409  LEASE_LOST          (stale generation, or the job already has a result)
503  ASYNC_UNAVAILABLE   (store down; nothing recorded)
```

Guarantees, each enforced by the existing store functions rather than new code paths:

* reuses `_tenant_api_key_guard`, `_lease_proof`, tenant+kind scoping, `classify_failure`, `fail_job` — no parallel safety mechanism;
* `result_json` is never written; no kernel is invoked; no `bridge_body` is accepted;
* a `COMPLETED` job is refused (409) so a failure can never sit beside an authoritative result;
* a repeat report on an already-`RETRY_WAIT`/`FAILED` job is idempotent and returns the stored state;
* the caller cannot influence retryability (no such field exists in the contract);
* no quota/metering charge (no job is created or claimed);
* the worker's message is bounded, single-line, and stored only in the error envelope.

### 9.2 `async_jobs.fail_exhausted_leases(tenant_id, kind)` + claim-path sweep

A tenant- and kind-scoped, expiry-gated, budget-gated `UPDATE` that moves an unclaimable orphan from `PROCESSING` to `FAILED` with code `LEASE_EXHAUSTED`. It runs opportunistically at the start of `claim_inference_job`, so no scheduler, background thread or new deployment surface is needed. A store failure there is logged and ignored: the claim below reports it honestly.

`LEASE_EXHAUSTED` is a new **terminal diagnostic** code. It is not added to any retryable set, and `public_status_for_error_code` already fails closed to `FAILED` for unknown codes.

### 9.3 Deployment note

The route is code-only: no migration, no schema change, no new table, no new column, no new environment variable. `platrixa_async_jobs` already carries every field used. Deploy the API before (or with) the worker; §4.3 covers the intermediate states.

---

## 10. Termux and Android — untouched

* No command was run on any phone; no `adb`, no SSH, no Termux invocation.
* No Termux configuration, `prefix`, `~/.bashrc`, service definition or startup script was read or written.
* No Android LLM app setting was changed; the model file was not modified or downloaded.
* The worker still defaults to the loopback bridge `http://127.0.0.1:8081` and still accepts the loopback llama.cpp endpoint `http://127.0.0.1:11434`.
* `normalize_model_url` still refuses every non-loopback host — verified continuously by `test_model_url_loopback_only_rejects_remote` and by the reliability suite's provenance tests.
* No tunnel, relay or public endpoint was created.

---

## 11. Railway production — untouched

* No Railway API call of any kind, read or write, was made during this sprint.
* No variable was read or changed; no plan, service, deployment, region or volume was touched.
* No deployment was triggered and no code was pushed.
* No API key was created, rotated or printed.
* No job was created, claimed, failed or submitted against production; all API tests ran against an embedded PostgreSQL instance in `/tmp` with the app constructed in-process.
* No real financial payload was sent anywhere; every fixture is synthetic.

---

## 12. Remaining steps for the device-level E2E test (yours to run)

1. **Deploy the API first** (`api/routes/inference_api.py`, `backend/auth/async_jobs.py`), then confirm `POST /v1/worker/jobs/{id}/failure` exists in the deployed OpenAPI document. Do not rely on the worker-route 404 fallback for long.
2. Copy `scripts/platrixa_outbound_inference_worker.py` to the Termux clone and run the offline suites there (`python3 scripts/platrixa_worker_reliability_test.py` etc.) before starting the worker — they need no network.
3. Start the Android LLM app with the model loaded, confirm `http://127.0.0.1:11434` answers, then start the worker with the existing flags plus `--model-provider <a label you trust for this device>`.
4. **Failure path:** with the model app not yet loaded, submit one synthetic job. Expect: worker logs "no submittable candidate (MODEL_UNAVAILABLE …)", one `POST …/failure`, and the job visible as `RETRY_WAIT` — not stuck in `PROCESSING`.
5. **Recovery path:** load the model, wait for the backoff window, and confirm the job is claimed and completes.
6. **Permanent path:** submit a synthetic job crafted to be unparseable by the model and confirm it ends `FAILED` with `VALIDATION_FAILED`, never `VERIFIED`.
7. **Provenance:** inspect the stored `execution_evidence` for that job and confirm the provider string is the one you configured, the model id is the configured id, and `artifact_identity` is still `PROVENANCE_UNKNOWN`.
8. **Sweep path:** claim a job, kill the worker with `SIGKILL` three times in a row (each time after the claim), then trigger one more claim; confirm the job ends `FAILED` with `LEASE_EXHAUSTED` rather than sitting in `PROCESSING`.
9. Only then run the full Termux → Android → Railway → kernel E2E with a real (non-synthetic) document under your own data-handling decision.

Step 4 and step 8 are the two that specifically prove this repair on the real device; everything else was proven here.

---

## 13. Invariant confirmation

| Invariant | Status |
|---|---|
| Model proposes interpretations only | Preserved — no new field is read from model output |
| Parser / evidence / schema / rules / kernel remain authoritative | Preserved — none of those files was modified |
| Model cannot assign `VERIFIED` | Preserved — the failure path writes no result at all |
| Missing/conflicting evidence stays explicit | Preserved |
| Failed inference never becomes a successful result | **Strengthened** — failure now writes an error envelope and clears the lease, with no `result_json` |
| Tenant authentication and job ownership unchanged | Preserved — same guard, same tenant derivation, same generation fence |
| Termux → Android loopback remains supported | Preserved and regression-tested |
| No kernel modification | Confirmed — `backend/kernel/**` untouched |
