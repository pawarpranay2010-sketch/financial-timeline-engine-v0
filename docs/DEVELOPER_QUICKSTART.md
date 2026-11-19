# Platrixa Developer API — Private Beta Quickstart

Platrixa turns accounting language ("Purchased furniture for cash Rs. 15,000")
into a **deterministically validated** accounting interpretation.

The model only *proposes*. The parser, grounding rules and accounting kernel
decide the outcome. A model response can never set `VERIFIED`, missing
information is never guessed, and an ambiguous transaction stays
`REVIEW_REQUIRED`.

Everything below is the public Developer API. You do not need to know anything
about Termux, Railway or the phone that runs the model.

- Base URL: `PLATRIXA_BASE_URL` (your invitation e-mail contains the exact host)
- Authentication header: `X-Platrixa-API-Key: plx_test_...`
- All request and response bodies are JSON.

Your API key is shown **once**, by the operator who invited you. Keep it in an
environment variable or a `0600` file — never in source control, never in a
browser.

---

## 1. Authentication

Every request carries your tenant key:

```bash
export PLATRIXA_BASE_URL="https://<your-beta-host>"
export PLATRIXA_API_KEY="plx_test_REPLACE_WITH_YOUR_KEY"
```

A request without a valid key is refused before any work happens. The key
identifies your tenant; you never send a tenant id, and you cannot read another
tenant's jobs or results.

**Never send this key from a browser.** The public website's API proxy always
authenticates as the website's own tenant and discards any key a browser
supplies, so a key pasted into browser devtools is ignored rather than honoured
— your request would quietly run as the website, not as you. Call the API from
your own backend (or proxy through it), with the key in an environment variable
or a secret manager.

Check that your credential and the service are ready:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/ready"
```

`200` means admission-ready. `503` means the deployment is alive but not ready
yet — retry later, do not change your request.

---

## 2. Submit ONE transaction

```bash
curl -sS -X POST "$PLATRIXA_BASE_URL/v1/inference-jobs" \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: one-tx-$(date +%s)" \
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'
```

```json
{
  "api_version": "v1",
  "job_id": "job_9f2c...",
  "result_id": "res_71ab...",
  "status": "PROCESSING",
  "status_url": "/v1/jobs/job_9f2c...",
  "result_url": "/v1/results/res_71ab...",
  "created_at": "2026-11-19T09:00:00+00:00"
}
```

`202 Accepted` means **admitted**, never "verified".

Python:

```python
import os, time, requests

BASE = os.environ["PLATRIXA_BASE_URL"]
KEY = os.environ["PLATRIXA_API_KEY"]
H = {"X-Platrixa-API-Key": KEY}

job = requests.post(
    f"{BASE}/v1/inference-jobs",
    headers={**H, "Idempotency-Key": "one-tx-0001"},
    json={"raw_input": "Purchased furniture for cash Rs. 15,000"},
    timeout=30,
)
job.raise_for_status()
job_id, result_id = job.json()["job_id"], job.json()["result_id"]
```

Node.js (18+):

```js
const BASE = process.env.PLATRIXA_BASE_URL;
const KEY = process.env.PLATRIXA_API_KEY;

const res = await fetch(`${BASE}/v1/inference-jobs`, {
  method: "POST",
  headers: {
    "X-Platrixa-API-Key": KEY,
    "Content-Type": "application/json",
    "Idempotency-Key": "one-tx-0001",
  },
  body: JSON.stringify({ raw_input: "Purchased furniture for cash Rs. 15,000" }),
});
const { job_id, result_id } = await res.json();
```

---

## 3. Submit a BATCH (many transactions in one job)

One job can carry a bounded batch. Do not confuse one job with one transaction.

```bash
curl -sS -X POST "$PLATRIXA_BASE_URL/v1/inference-jobs" \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: batch-2026-11-19-a" \
  -d '{
    "mode": "independent",
    "transactions": [
      {"transaction_id": "tx-001", "input": "Started business with cash Rs. 10000"},
      {"transaction_id": "tx-002", "input": "Purchased goods for cash Rs. 2000"},
      {"transaction_id": "tx-003", "input": "Sold goods for cash Rs. 3000"}
    ]
  }'
```

Two modes:

| Mode | Use it when | Execution |
| --- | --- | --- |
| `independent` (default) | transactions are unrelated | each is validated on its own; results are independent |
| `ordered` | the transactions form one accounting sequence | processed in order; each one receives the **approved** prior state; a non-verified link halts the batch |

Batch rules:

- `transactions` must be a non-empty list; `transaction_id` must be unique
  inside the batch and match `^[A-Za-z0-9._:-]{1,64}$`;
- each `input` is 1–2000 characters; the whole request is bounded by the
  deployment's byte, transaction-count and summed-input limits (returned in the
  `details` of a rejection);
- never send `raw_input` and `transactions` together (`BATCH_AMBIGUOUS`).

Python:

```python
batch = requests.post(
    f"{BASE}/v1/inference-jobs",
    headers={**H, "Idempotency-Key": "batch-0001"},
    json={
        "mode": "ordered",
        "transactions": [
            {"transaction_id": "tx-001", "input": "Started business with cash Rs. 10000"},
            {"transaction_id": "tx-002", "input": "Purchased goods for cash Rs. 2000"},
        ],
    },
    timeout=30,
)
batch.raise_for_status()
payload = batch.json()          # job_id, result_id, mode, transaction_count
```

---

## 4. Receive the job ID, then 5. poll job status

Poll the `status_url` (or build `/v1/jobs/{job_id}`). Use the returned
`retry_after`-style pacing: every 2–5 seconds, with backoff.

```bash
curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/jobs/job_9f2c..."
```

A batch job additionally reports per-outcome counts:

```json
{
  "api_version": "v1",
  "job_id": "job_9f2c...",
  "status": "PROCESSING",
  "batch": {
    "mode": "independent",
    "total_transactions": 3,
    "counts": {"verified": 1, "review_required": 1, "unsupported": 0,
               "failed": 0, "blocked": 0, "deferred": 1, "pending": 0},
    "transactions_url": "/v1/results/res_71ab.../transactions"
  }
}
```

`status` is `PROCESSING`, `RETRY_WAIT` or a terminal state. `RETRY_WAIT` means a
retryable infrastructure failure was recorded and the queue will try again; do
not resubmit.

---

## 6. Retrieve results

Single transaction:

```bash
curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/results/res_71ab..."
```

Batch:

```bash
# compact per-transaction summaries (paginated)
curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/results/res_71ab.../transactions?limit=50&offset=0"

# one transaction's full authoritative envelope
curl -sS -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  "$PLATRIXA_BASE_URL/v1/results/res_71ab.../transactions/tx-002"
```

The batch envelope carries `batch_status` (`COMPLETE`, `PARTIAL`, `HALTED`),
the per-outcome counts, and the state chain in ordered mode. `api_status` is
`VERIFIED` only when **every** transaction is verified.

---

## 7. Handle `REVIEW_REQUIRED` (and other non-verified outcomes)

| `api_status` | Meaning | What to do |
| --- | --- | --- |
| `VERIFIED` | schema, grounding, capability and the deterministic kernel all agreed | use the `accounting` block |
| `REVIEW_REQUIRED` | insufficient evidence/capability; a human must decide | queue it for review; **do not post it** |
| `UNSUPPORTED` | outside the supported boundary | do not post; surface to the user |
| `INVALID_INPUT` | the input/candidate failed validation | fix the input or let a human review |
| `FAILED` | the engine reported failure | check `reason_codes`; retry only if `retryable` |

In a batch, one transaction's outcome never changes another's. In `ordered`
mode a non-verified link marks every later transaction `BLOCKED` with
`BLOCKED_BY_PRIOR_UNVERIFIED_STATE` — those transactions were never sent to the
model, and they must be resubmitted after the blocking link is resolved.

Note: `accounting` is populated only when the deterministic authority ran; it
is `null` for `REVIEW_REQUIRED`/`UNSUPPORTED`/`INVALID_INPUT`. Treat it as the
authority output, not as tax or legal advice.

---

## 8. Handle quota errors

| HTTP | Code | Meaning |
| --- | --- | --- |
| `429` | `QUOTA_EXHAUSTED` | your monthly **job** allowance is spent |
| `429` | `TRANSACTION_QUOTA_EXCEEDED` | your monthly **accounting-transaction** allowance is spent (`details.limit/used/requested`) |
| `429` | `TENANT_RATE_LIMITED` | too many requests in the current window (`Retry-After`) |
| `503` | `GLOBAL_QUEUE_FULL` / `TENANT_QUEUE_FULL` | the deployment queue is full — retry later |

```python
if r.status_code == 429:
    code = r.json()["error"]["code"]
    if code == "TENANT_RATE_LIMITED":
        time.sleep(int(r.headers.get("Retry-After", "5")))
    elif code in ("QUOTA_EXHAUSTED", "TRANSACTION_QUOTA_EXCEEDED"):
        raise SystemExit("quota exhausted — contact the Platrixa operator")
```

Transaction units are charged at admission, one per admitted transaction — a
batch of 3 costs 3 units (and 1 job unit). A rejected batch costs nothing.

---

## 9. Retries and idempotency

Send an `Idempotency-Key` on creation. It is the contract that makes a retry
safe:

- same key + identical body → the **original** job reference, no new job, no
  extra quota (header `Idempotent-Replayed: true`);
- same key + different body → `409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST`;
- a retry after a timeout is always safe: use the same key.

```python
def submit_with_retry(body, idem, attempts=4):
    for attempt in range(attempts):
        try:
            r = requests.post(f"{BASE}/v1/inference-jobs",
                              headers={**H, "Idempotency-Key": idem},
                              json=body, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (202, 409):
            return r
        if r.status_code in (429, 503):
            time.sleep(int(r.headers.get("Retry-After", 2 ** attempt)))
            continue
        r.raise_for_status()
    raise RuntimeError("submission did not succeed")
```

Batch idempotency is per batch: the fingerprint covers the mode and every
`(transaction_id, input)` pair.

---

## 10. Revoke and replace a key

Only the Platrixa operator can revoke or rotate a key (this is a private beta;
there is no self-service signup or billing). Ask the operator, who will:

1. revoke the old key — it stops authenticating immediately;
2. issue a new key, shown **once**.

Rotating a key resets its monthly usage bucket. Plan the switch during a quiet
window: jobs created with the old key remain readable only by that tenant, but
the new key is a new credential, not a new tenant.

---

## Errors you should expect while integrating

| Code | HTTP | Meaning |
| --- | --- | --- |
| `REQUEST_MALFORMED` | 400 | body is not valid JSON/shape |
| `BATCH_EMPTY` | 400 | `transactions` is empty |
| `BATCH_TOO_MANY_TRANSACTIONS` | 400 | over the configured batch size |
| `BATCH_DUPLICATE_TRANSACTION_ID` | 400 | repeated `transaction_id` (named in `details`) |
| `BATCH_TRANSACTION_INVALID` | 400 | bad id/input at `details.index` |
| `BATCH_TOO_LARGE` | 400 | request bytes over the limit |
| `BATCH_INPUT_TOO_LARGE` | 400 | summed input characters over the limit |
| `BATCH_MODE_INVALID` | 400 | mode is not `independent`/`ordered` |
| `BATCH_AMBIGUOUS` | 400 | `raw_input` and `transactions` both present |
| `IDEMPOTENCY_KEY_INVALID` | 400 | key must be 16–200 url-safe characters |
| `RESULT_NOT_READY` | 404 | the job has not produced that result yet |
| `RESULT_NOT_FOUND` | 404 | not your tenant, or no such result |
| `NOT_A_BATCH_JOB` | 400 | batch-only route used on a single-transaction job |
| `ASYNC_UNAVAILABLE` | 503 | the durable store is unavailable — retry |

Full schemas and limits: [DEVELOPER_API_CONTRACT.md](DEVELOPER_API_CONTRACT.md).
Batch semantics and quotas: [BATCH_JOBS.md](BATCH_JOBS.md).

A runnable client lives at `examples/developer_batch_client.py`.
