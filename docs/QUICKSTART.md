# Platrixa Quickstart

From zero to a validated financial-semantic result in five calls.

> All examples use placeholders (`YOUR_API_KEY`, `YOUR_IDEMPOTENCY_KEY`) —
> never real secrets. Keys are issued by the operator; there is no
> self-serve signup yet.

---

## 0. Base URL

```bash
HOST=https://financial-timeline-engine-v0-production.up.railway.app
# local server: HOST=http://127.0.0.1:8000
#   python -m uvicorn api.main:app --port 8000
```

## 1. Obtain an API key

Ask the operator for a tenant key during the private beta. You receive a
key of the form `plx_live_…` **exactly once** — Platrixa stores only its
SHA-256 hash and cannot show it again. Each key belongs to one tenant, so
two developers never share keys, jobs, or quota.

Quota is enforced **per key**, not per tenant: every key is its own row in
the quota store (`api_key_hash` is the primary key) and starts with **100
admitted requests per UTC month**. Issuing a second key for the same
tenant therefore adds a second, independent bucket — it does not raise the
first key's limit, and no API changes a limit. Rotation
(`POST /v1/developer/api-keys/{id}/rotate`) replaces the credential but
**preserves the limit, the used count and the month bucket** — it never
grants a fresh allowance.

<details>
<summary>Operator side (developers can skip this)</summary>

```bash
# management surface — requires the operator token and gateway assertion
curl -s -X POST "$MGMT_HOST/v1/developer/api-keys" \
  -H "X-Platrixa-Management-Token: $MGMT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "acme-dev", "environment": "live"}'
# → 201 { "id": "…", "key": "plx_live_…", "key_prefix": "…", "status": "ACTIVE" }

# revoke / rotate (raw key shown once on rotate)
curl -s -X POST "$MGMT_HOST/v1/developer/api-keys/KEY_ID/revoke" -H "X-Platrixa-Management-Token: $MGMT_TOKEN"
curl -s -X POST "$MGMT_HOST/v1/developer/api-keys/KEY_ID/rotate" -H "X-Platrixa-Management-Token: $MGMT_TOKEN"
```

For a purely local server the same credential can be seeded directly:

```bash
PLATRIXA_METERING_DATABASE_URL=postgresql://… \
  python -m backend.auth.dev_seed_tenant --tenant acme-dev --limit 2000
```

</details>

## 2. Send a transaction

```bash
curl -s -X POST "$HOST/v1/process" \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: YOUR_API_KEY" \
  -H "Idempotency-Key: YOUR_IDEMPOTENCY_KEY" \
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'
```

## 3. Receive the result

```json
{
  "api_version": "v1",
  "request_id": "req-123",
  "status": "VERIFIED",
  "api_status": "VERIFIED",
  "engine_status": "VERIFIED",
  "success": true,
  "retryable": false,
  "reason_codes": [],
  "interpretation": {
    "transaction_type": "PURCHASE",
    "amounts": [{"value": "15000", "source": "explicit", "value_origin": "EXTRACTED"}]
  },
  "accounting": {"debit_lines": [{"account": "Furniture", "amount": 15000}],
                 "credit_lines": [{"account": "Cash", "amount": 15000}]},
  "accounting_result": {"debit_lines": [{"account": "Furniture", "amount": 15000}],
                        "credit_lines": [{"account": "Cash", "amount": 15000}]},
  "evidence": [],
  "metadata": {"engine_status": "VERIFIED", "processing_time_ms": 41}
}
```

## 4. Inspect the status

Act on `api_status` only:

| `api_status` | Meaning | Do |
|---|---|---|
| `VERIFIED` | deterministic authority passed | you may use `accounting_result` |
| `REVIEW_REQUIRED` | insufficient evidence/capability | queue for human review — never auto-post |
| `UNSUPPORTED` | outside the supported boundary | do not retry |
| `INVALID_INPUT` | request invalid | fix the request |
| `FAILED` | deterministic rejection / server failure | read `reason_codes` |
| `PROCESSING` | not yet authoritative | retry later or poll |

VERIFIED means the schema/grounding/capability/authority requirements
were satisfied — **not** legal, tax, or accounting compliance, and never
advice.

## 5. Inspect evidence (documents)

For document inputs the result carries deterministic citations:

```json
"evidence": [
  {"evidence_id": "doc_invoice_p1_e0003", "document_id": "doc_invoice_ab12cd34",
   "page": 1, "text": "Total: Rs. 15,000",
   "bbox": [12.0, 300.5, 200.0, 318.25], "extraction_confidence": 0.98,
   "source_type": "pypdf"}
],
"lineage": {"amounts": ["doc_invoice_p1_e0003"]}
```

`bbox`/`extraction_confidence` are `null` when the engine had none —
never invented. Check `lineage` before trusting an extracted amount, and
remember `interpretation.amounts[].value_origin` is `EXTRACTED`
(model-suggested), while `accounting_result` is deterministic.

## 6. Act according to the status

- **VERIFIED** → post `accounting_result` into your workflow.
- **REVIEW_REQUIRED** → human review queue.
- **UNSUPPORTED** → reject or route elsewhere; check `/v1/capabilities`.
- **FAILED** → read `reason_codes`; retry only if the code says retryable.
- **PROCESSING** → back off and retry/poll.

---

## Async text jobs (the hosted inference path)

Synchronous `/v1/process` needs the interpretation model inline. On the
hosted deployment interpretation runs on an outbound worker (a Termux
device running llama.cpp), so text jobs are queued and polled instead.

```bash
# 1. submit — 202 means ADMITTED, never VERIFIED
curl -s -X POST "$HOST/v1/inference-jobs" \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: YOUR_API_KEY" \
  -H "Idempotency-Key: YOUR_IDEMPOTENCY_KEY" \
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'
```

```json
{
  "api_version": "v1",
  "request_id": "req-9",
  "job_id": "job_…",
  "result_id": "res_…",
  "status": "PROCESSING",
  "status_label": "Processing",
  "status_url": "/v1/jobs/job_…",
  "result_url": "/v1/results/res_…",
  "created_at": "2026-11-16T12:00:00+00:00"
}
```

```bash
# 2. poll until the job leaves PROCESSING
curl -s "$HOST/v1/jobs/job_…" -H "X-Platrixa-API-Key: YOUR_API_KEY"
```

```json
{
  "api_version": "v1",
  "job_id": "job_…",
  "request_id": "req-9",
  "status": "VERIFIED",
  "status_label": "Verified",
  "retryable": false,
  "reason_codes": [],
  "result_url": "/v1/results/res_…",
  "created_at": "2026-11-16T12:00:00+00:00",
  "updated_at": "2026-11-16T12:00:24+00:00"
}
```

`status` is `PROCESSING` while the job is queued **or** running, then the
real engine outcome (`VERIFIED`, `REVIEW_REQUIRED`, `UNSUPPORTED`,
`FAILED`). `RETRY_WAIT` means the worker lease lapsed and the job will be
re-claimed. A `202` is an admission, never a promise of VERIFIED.

```bash
# 3. retrieve the stored result (same envelope as /v1/process)
curl -s "$HOST/v1/results/res_…" -H "X-Platrixa-API-Key: YOUR_API_KEY"
```

Before completion this returns `404 RESULT_NOT_READY`; an id owned by
another tenant returns `404 RESULT_NOT_FOUND` — never data.

## Async documents (optional)

```bash
# submit
curl -s -X POST "$HOST/v1/documents" \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: YOUR_API_KEY" \
  -d '{"document_b64": "<base64 pdf>", "source_name": "invoice.pdf"}'
# → 202 {"job_id": "job_…", "result_id": "res_…", "status_url": "/v1/jobs/job_…", …}

# poll (completion is NOT VERIFIED — the status field tells the truth)
curl -s "$HOST/v1/jobs/job_…" -H "X-Platrixa-API-Key: YOUR_API_KEY"

# fetch the same 5D result envelope
curl -s "$HOST/v1/results/res_…" -H "X-Platrixa-API-Key: YOUR_API_KEY"
```

## Discover capabilities (optional)

```bash
curl -s "$HOST/v1/capabilities" -H "X-Platrixa-API-Key: YOUR_API_KEY"
```

Deterministic, read-only listing of what the runtime can prove today —
including `UNSUPPORTED`/`PLANNED` metadata. Do not hardcode assumptions.

---

## Errors and quota

Every failure uses one envelope:

```json
{
  "api_version": "v1",
  "error": {
    "code": "UNAUTHORIZED",
    "message": "missing or invalid API key",
    "api_status": "INVALID_INPUT",
    "api_status_label": "Invalid input",
    "retryable": false
  }
}
```

| HTTP | Code | Do |
|---|---|---|
| 400 | `REQUEST_MALFORMED` | fix the JSON body |
| 401 | `UNAUTHORIZED` | check `X-Platrixa-API-Key` — missing and unknown keys are indistinguishable |
| 404 | `JOB_NOT_FOUND` / `RESULT_NOT_FOUND` | wrong id, or another tenant's id |
| 404 | `RESULT_NOT_READY` | keep polling the job |
| 409 | `IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST` | use a new key per distinct request |
| 413 | `REQUEST_TOO_LARGE` | shrink the input |
| 422 | `INPUT_INVALID` | input outside the accepted domain — do not retry unchanged |
| 429 | `QUOTA_EXHAUSTED` | monthly bucket empty; wait for the next UTC month |
| 503 | `METERING_UNAVAILABLE` / `ASYNC_UNAVAILABLE` / `PROVIDER_UNAVAILABLE` | fail-closed outage; retry with backoff |

Quota is consumed once per **admitted** request (sync and async alike);
authentication failures cost nothing. With a 100/month key, `GET /v1/usage`
returns the authenticated key's current bucket without spending quota.

---

**Trust & safety:** Platrixa is financial semantic validation
infrastructure, not a financial/investment/tax/legal adviser and not a
regulatory authority. A VERIFIED result does not establish compliance.
See `docs/DEVELOPER_GUIDE.md` §19 and `docs/HOSTED_API.md` for the full
contract and known limitations.
