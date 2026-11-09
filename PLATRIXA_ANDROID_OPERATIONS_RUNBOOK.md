# Platrixa — Android Inference Worker Operations Runbook

Operational facts for running the outbound worker against the Android LLM app's
OpenAI-compatible server, and for reading what the API reports while it does.

Companion documents: `PLATRIXA_ANDROID_MODEL_WORKER_INTEGRATION_REPORT.md`
(request/response contract) and `PLATRIXA_WORKER_RELIABILITY_REPAIR_REPORT.md`
(failure reporting and lease exhaustion).

---

## 1. Startup order

The worker only ever makes outbound requests, so the model server must be up
**before** the worker claims anything — otherwise the first job fails with a
reported `MODEL_UNAVAILABLE`.

1. **API** — deployed and healthy (`GET /v1/health`, `GET /v1/ready` both 200).
2. **Android LLM app** — GGUF loaded, server listening on `127.0.0.1:11434`.
   Verify from Termux, not from a desktop:
   ```bash
   curl -s http://127.0.0.1:11434/v1/models
   ```
   `{"data":[{"id":...}]}` → `--model-id auto` will work.
   404 → your app has no `/v1/models`; use an explicit `--model-id`.
3. **Worker** — start it last. Optional preflight before the first job:
   ```bash
   python3 scripts/platrixa_outbound_inference_worker.py --once --verbose
   ```
4. **Shutdown** — stop the worker first (Ctrl-C / `SIGTERM`, exit 130), then the
   model server. Stopping the model server first only costs one reported
   `MODEL_UNAVAILABLE`; it does not corrupt anything.

Never start two workers with the same `--worker-id` against one tenant: they
would both claim, and one would lose every lease it did not renew. Distinct
ids are the fencing key.

---

## 2. Environment

Required:

| Variable | Meaning |
|---|---|
| `PLATRIXA_API_BASE_URL` | API origin (`https://…` for anything non-loopback) |
| `PLATRIXA_API_KEY` | tenant key; the only credential that reaches the API |
| `PLATRIXA_WORKER_ID` | stable, unique, `[A-Za-z0-9._-]{1,128}` (defaults to host-based) |

Direct-model path:

| Variable | Default | Meaning |
|---|---|---|
| `PLATRIXA_WORKER_MODEL_URL` | unset → device bridge | must be loopback (`127.0.0.1`, `localhost`, `::1`) |
| `PLATRIXA_WORKER_MODEL_DIALECT` | `chat` | `chat` = `/v1/chat/completions`, `completion` = `/v1/completions` |
| `PLATRIXA_WORKER_MODEL_ID` | `default` | model id sent to the endpoint; `auto` = resolve from `GET /v1/models` at startup |
| `PLATRIXA_WORKER_MODEL_TIMEOUT_S` | `120` | per-request timeout (connect and read share it) |
| `PLATRIXA_WORKER_MODEL_PROVIDER` | `android-llama-server` | provenance label recorded for this backend; `unknown` → `PROVENANCE_UNKNOWN` |
| `PLATRIXA_WORKER_MODEL_KEY` | unset | bearer token for the model server, if it requires one |

Retry tuning: `PLATRIXA_WORKER_RATE_LIMIT_MAX_RETRIES` (default 2 extra
attempts after a 429) and `PLATRIXA_WORKER_RATE_LIMIT_MAX_WAIT_S` (default 60,
hard ceiling on any single 429 wait, including a `Retry-After` hint).

Bridge mode (the older device-bridge app) uses
`PLATRIXA_LOCAL_BRIDGE_URL` / `PLATRIXA_LOCAL_BRIDGE_KEY` **instead of** the
model URL; setting both is a configuration mistake — the direct model wins and
the bridge is never called.

`--model-id auto` and `--model-provider` have CLI equivalents; a CLI flag always
beats the environment. A bad configuration exits **2** before any network call.

---

## 3. Health checks and expected failure behaviour

| Signal | Meaning | Action |
|---|---|---|
| worker exit `0` | ran (jobs may still have failed — see the run report) | none |
| worker exit `1` | uncaught crash; traceback on stderr | read the traceback, file a bug |
| worker exit `2` | configuration error (bad URL, bad model discovery, bad key file) | fix the setting; nothing was claimed |
| worker exit `3` | fatal claim rejection (401/403) | check `PLATRIXA_API_KEY`; the worker will not retry |
| worker exit `130` | interrupted | none |
| run report `failures reported > 0` | the server recorded classified failures | normal under a model outage |
| run report `failure report failures > 0` | the API refused/unreachable for a report | the lease will expire and the job be reclaimed |
| API `GET /v1/ready` 503 | durable store unavailable | the worker's own calls will also fail closed |

Job states as the API reports them (`GET /v1/jobs/{job_id}`):

| `status` | `retryable` | Meaning |
|---|---|---|
| `PROCESSING` | true | queued, claimed, or being worked |
| `RETRY_WAIT` | true | a retryable failure was recorded; retry already scheduled |
| `FAILED` | false | terminal; `reason_codes` names the cause |
| engine status (`VERIFIED` / `REVIEW_REQUIRED` / `UNSUPPORTED`) | false | completed with a real verdict; fetch the envelope |

`RETRY_WAIT` is **not** an error condition and needs no operator action. Never
treat it as terminal; do not re-submit the same work because of it.

Failure codes the worker reports, with the retry the server applies:

| Code | Retryable | Typical cause |
|---|---|---|
| `MODEL_UNAVAILABLE` | yes | model server down, 5xx, timeout, 429 budget spent |
| `PROVIDER_UNAVAILABLE` | yes | provider-level unavailability |
| `INTERNAL_ERROR` | yes | unexpected worker fault |
| `VALIDATION_FAILED` | no | empty/oversized/malformed answer, missing or unusable finish reason, stream without `[DONE]` |
| `UNAUTHORIZED` | no | model endpoint rejected the credential (401/403) |
| `REQUEST_MALFORMED` | no | model endpoint refused the request shape (400/404/422) |

Truncation is special: the worker labels `completion_status: TRUNCATED` and
submits it; the server's shared parser refuses it and the kernel records
`VALIDATION_FAILED` with no interpretation and no accounting.

A job that exhausts its attempt budget and loses its lease is terminalised by
the claim-path sweep with `LEASE_EXHAUSTED` — it cannot sit in `PROCESSING`
forever.

---

## 4. API configuration the worker depends on

* Durable async store configured (`async_jobs.async_configured()`); without it
  every worker call answers `ASYNC_NOT_CONFIGURED` / `ASYNC_UNAVAILABLE`.
* The tenant key must exist and be active, with quota headroom for the jobs you
  intend to run.
* Inference jobs are created by `POST /v1/inference-jobs`; the worker only needs
  `/claim`, `/lease`, `/result` and `/failure`.
* No schema change was introduced by the reliability repair, and none is needed
  for this worker.
* The **API must be deployed before** a worker that relies on `/failure` is
  rolled out. A repaired worker against an old API still runs, but a failed
  generation can only be recovered by lease expiry, and a job whose budget is
  spent can strand (the original defect).

---

## 5. Log hygiene

What is guaranteed by the code and pinned by tests:

* The tenant API key is **never** sent to the model server, and never appears in
  worker stdout/stderr (`_redact` masks it; the lifecycle suite asserts its
  absence end to end).
* Bridging credentials and model keys are kept in separate header sets: the
  bridge key never goes to the API, the tenant key never goes to the model.
* Failure reports carry a fixed taxonomy description — no exception text, no
  model output, no financial payload — bounded to 200 single-line characters.
* The model server is loopback only, so the prompt and answer never leave the
  phone.

What is **not** guaranteed: the API stores the job's `raw_input` (it must, to
run the pipeline) and the deterministic result envelope. That is the same
handling as every other Platrixa path; treat the deployment and its database
accordingly.

---

## 6. First controlled run

```bash
export PLATRIXA_API_BASE_URL="https://<api-origin>"
export PLATRIXA_API_KEY="<tenant key>"
export PLATRIXA_WORKER_ID="$(hostname)-worker-1"

python3 scripts/platrixa_outbound_inference_worker.py \
  --model-url http://127.0.0.1:11434 \
  --model-dialect chat \
  --model-id auto \
  --model-provider phone-llamacpp \
  --model-timeout 120 \
  --once --verbose
```

Expected on success: exit `0`, one claim, one model call, and a job that ends at
an engine status rather than `FAILED`. Then poll once:

```bash
curl -s "https://<api-origin>/v1/jobs/<job_id>" -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY"
```

Run it four times in a row before trusting it unattended: once with the model
server stopped (expect `RETRY_WAIT` + `MODEL_UNAVAILABLE`), once with it started
(expect a real verdict), once with the worker interrupted mid-claim (expect the
job reclaimed after the lease), and once after the retry budget is spent (expect
`LEASE_EXHAUSTED`, not a stuck `PROCESSING`).
