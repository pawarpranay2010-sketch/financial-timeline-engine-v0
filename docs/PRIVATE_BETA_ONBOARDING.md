# Private Beta Onboarding — one developer at a time

This checklist onboards **one** of the first 3–4 invited developers. It assumes
the API is already deployed and the Android inference worker is running — the
developer never touches Termux, Railway, the phone, or the model server.

**What does not exist (do not imply otherwise):** public sign-up, developer
login, a developer dashboard, self-service key creation, billing, or a hosted
model endpoint for developers. Every key is issued by the operator, and every
developer gets a separate tenant.

Two audiences, two lists:

| Operation | Who can do it |
| --- | --- |
| Provision a tenant, set quotas, set rate limits | **Operator only** (management token) |
| Create / rotate / revoke an API key | **Operator only** (management token) |
| Read any tenant's usage, keys, queue | **Operator only** |
| Submit jobs, poll status, list own jobs, read own results | Developer (tenant key) |
| Read own usage (`GET /v1/usage`) | Developer (tenant key) |
| Read all tenants' usage (`GET /v1/owner/usage`) | **Operator only** |

---

## A. Operator checklist (once per developer)

Copy this block into the operator's tracker and tick every line.

- [ ] Confirm the developer's identity and the application they intend to build.
- [ ] Provision a **separate tenant** for that developer:
      `POST /v1/developer/tenants` with `tenant_id`, `developer_name`,
      `environment`, `monthly_limit` (jobs), `transaction_monthly_limit`
      (accounting transactions).
- [ ] Set an explicit **job quota** (`monthly_limit`) for the beta window.
- [ ] Set an explicit **transaction quota** (`transaction_monthly_limit`).
- [ ] Set an appropriate **rate limit** (`PLATRIXA_TENANT_RATE_LIMIT` /
      `PLATRIXA_TENANT_RATE_WINDOW_SECONDS` on the deployment; record the value
      the developer can expect).
- [ ] Create the tenant's **API key** through the management API
      (`POST /v1/developer/tenants` returns it, or
      `POST /v1/developer/api-keys/{key_id}/rotate` issues a replacement).
- [ ] Deliver the **raw key out of band** (password manager / one-time link —
      never email body, never chat, never a ticket comment).
- [ ] Tell the developer the key is **shown once** and cannot be retrieved
      later; if it is lost, the operator rotates it.
- [ ] Confirm the developer stores it in a **backend environment variable or
      secret manager**, never in browser code or a mobile binary.
- [ ] Explain **revocation and replacement**: revocation stops authentication
      immediately; rotation issues a new one-time secret and resets that
      credential's usage bucket.
- [ ] Record `tenant_id`, developer contact, quota values, rate limit and
      invitation status **without recording the raw key**.
- [ ] Send the developer: `docs/DEVELOPER_QUICKSTART.md`,
      `docs/DEVELOPER_API_CONTRACT.md`, `docs/BATCH_JOBS.md`, and the base URL.
- [ ] Confirm the developer has completed the first-integration sequence below
      (or book a session to do it together).

Operator verification (no secrets printed):

```bash
# masked key list for the operator's own tenant
curl -sS "$PLATRIXA_BASE_URL/v1/developer/api-keys" \
  -H "X-Platrixa-Management-Token: $PLATRIXA_MANAGEMENT_TOKEN"

# per-tenant jobs, transactions, quotas and backlog
curl -sS "$PLATRIXA_BASE_URL/v1/owner/usage" \
  -H "X-Platrixa-Management-Token: $PLATRIXA_MANAGEMENT_TOKEN"
```

---

## B. Developer checklist (once per integration)

- [ ] Read `docs/DEVELOPER_QUICKSTART.md` and `docs/DEVELOPER_API_CONTRACT.md`.
- [ ] Integrate from a **trusted backend** — not browser JavaScript, not a
      public mobile app.
- [ ] Store the key in an environment variable or secret manager; never commit
      it, never put it in a URL, never log it.
- [ ] Submit a basic test job (`POST /v1/inference-jobs` with `raw_input`).
- [ ] Poll the job (`GET /v1/jobs/{job_id}`) until it is terminal.
- [ ] Retrieve and inspect the result (`GET /v1/results/{result_id}`).
- [ ] Handle `REVIEW_REQUIRED`, `UNSUPPORTED`, `INVALID_INPUT` and `FAILED`
      correctly — completion is **not** approval, and only the deterministic
      kernel can produce `VERIFIED`.
- [ ] Handle `429` (`QUOTA_EXCEEDED`, `TRANSACTION_QUOTA_EXCEEDED`) and honour
      `Retry-After`.
- [ ] Use `Idempotency-Key` for every retryable submission.
- [ ] Understand the difference between `independent` and `ordered` batches,
      and what `BLOCKED` / `DEFERRED` mean.
- [ ] Handle partial batch outcomes; never treat a batch envelope as "all
      verified" unless `success` is true.
- [ ] Never treat model-generated text as verified accounting output.
- [ ] Build the job-history screen from `GET /v1/jobs` instead of storing every
      job id locally.
- [ ] Read `GET /v1/usage` and understand the two units (jobs vs accounting
      transactions) before setting alerts.

---

## C. First-integration sequence (about 15 minutes)

```bash
export PLATRIXA_BASE_URL="https://<your-beta-host>"
export PLATRIXA_API_KEY="plx_test_REPLACE_WITH_YOUR_KEY"   # server-side only

# 0. admission readiness (200 ready, 503 not ready yet)
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/ready"

# 1. submit ONE transaction
JOB=$(curl -sS -X POST "$PLATRIXA_BASE_URL/v1/inference-jobs" \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  -H "Idempotency-Key: onboarding-$(date +%s)" \
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])')
echo "$JOB"

# 2. poll until terminal (PROCESSING → COMPLETED or FAILED)
curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/jobs/$JOB"

# 3. list your own jobs (this is what a history screen needs)
curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/jobs?limit=20"

# 4. fetch the result once the job is COMPLETED (result_url from step 2)
# curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
#   "$PLATRIXA_BASE_URL/v1/results/<result_id>"

# 5. inspect your own usage — jobs and transactions are separate units
curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/usage"
```

Then repeat step 1 with the **same** `Idempotency-Key` and the same body: the
response must carry the same `job_id` and `Idempotent-Replayed: true`, with no
second job and no second charge. That single check proves the retry contract the
developer will rely on in production.

A runnable client (batch submission + polling) lives at
`examples/developer_batch_client.py`.

---

## D. What to tell the developer about the inference architecture

Platrixa's model runs on an Android device operated by the operator. The
developer does **not** need Termux, a tunnel, a phone, or model credentials:

- the API accepts the job and returns a `job_id` (202);
- the queue is durable, so a restart or a busy phone delays work but never
  loses it;
- `PROCESSING` means "not finished yet", not "failed";
- the worker's model server is bound to the phone's loopback interface
  (`127.0.0.1:11434`) and is unreachable from the internet by design;
- if the phone is offline, jobs stay `QUEUED` and are processed when it
  returns — the API does not silently downgrade or fabricate a result.

Do not promise throughput, latency or four-way concurrency before the
real-device measurements in `docs/ANDROID_PRIVATE_BETA_DEVICE_TEST.md` are
recorded.
