# Platrixa Developer Guide

The complete guide to the Platrixa hosted developer API (`/v1`).
For a 5-minute minimal path, see [`QUICKSTART.md`](QUICKSTART.md).

---

## 1. What Platrixa does

Platrixa is **financial semantic validation infrastructure**. You send
financial language (a transaction string, or a document such as an
invoice), and Platrixa returns a structured, validated,
evidence-grounded result with an explicit status:

```
input (financial language / document)
   ↓
model interpretation (LLM — suggests only)
   ↓
Candidate Semantic IR (18-field structured contract)
   ↓
schema verification (strict, fail-closed)
   ↓
grounding / evidence validation (fail-closed)
   ↓
deterministic authority
   ↓
VERIFIED / REVIEW_REQUIRED / UNSUPPORTED  →  result + evidence
```

**Core principle:** AI understands; Platrixa validates; deterministic
authorities calculate and execute. The model is never the financial
authority, and a VERIFIED result never means the model "decided".

## 2. What Platrixa does NOT do

- It is not a financial, investment, tax, or legal adviser.
- It is not an autonomous accountant or a regulatory authority.
- VERIFIED does **not** establish legal, tax, regulatory, or factual
  compliance — it means the request satisfied the implemented schema,
  grounding, capability, and deterministic-authority requirements.
- When sufficient evidence or capability cannot be established, Platrixa
  returns `REVIEW_REQUIRED` or `UNSUPPORTED` instead of inventing a
  conclusion.
- It does not promise exactly-once execution (see idempotency below).

## 3. Authentication

All `/v1` processing endpoints authenticate with a tenant API key
provisioned by the operator (no self-serve issuance yet):

```
X-Platrixa-API-Key: YOUR_API_KEY
```

- Keys are stored server-side as SHA-256 hashes only; the raw key is
  shown exactly once at provisioning.
- Missing/unknown key → `401 UNAUTHORIZED` (externally indistinguishable).
- Monthly quota exhausted → `429 QUOTA_EXHAUSTED` (one unit per admitted
  request; auth failures consume zero).
- Metering store unavailable → `503 METERING_UNAVAILABLE` (fail closed —
  never admitted).

## 4. API base URL

```
HOST = http://127.0.0.1:8000        # local: python -m uvicorn api.main:app --port 8000
```

All developer endpoints live under `/v1`. `GET /v1/health` (liveness)
and `GET /v1/ready` (readiness) need no key.

## 5. POST /v1/process — synchronous transaction

```bash
curl -s -X POST "$HOST/v1/process" \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: YOUR_API_KEY" \
  -d '{"raw_input": "Purchased furniture for cash Rs. 15,000"}'
```

The body has exactly one field, `raw_input` (1–2000 chars). Transport
errors: `400 REQUEST_MALFORMED` (malformed JSON), `422 INPUT_INVALID`
(domain-invalid input), `401`, `429`, `503`.

## 6. POST /v1/process/document — synchronous document

```bash
curl -s -X POST "$HOST/v1/process/document" \
  -H "X-Platrixa-API-Key: YOUR_API_KEY" \
  -F "document=@invoice.pdf"
```

Multipart upload (PDF/PNG/JPEG/TIFF/BMP/WEBP ≤ 10 MiB) or JSON with
`raw_input` text. `415` unsupported type, `413` too large, `400` no/both
inputs. The response carries document `evidence` (page, bbox, confidence
— never fabricated) and `lineage` (field → evidence ids).

## 7. POST /v1/documents — asynchronous submission (202)

For long documents, submit asynchronously and poll:

```bash
curl -s -X POST "$HOST/v1/documents" \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: YOUR_API_KEY" \
  -d '{"document_b64": "<base64>", "source_name": "invoice.pdf"}'
```

Response `202 Accepted`:

```json
{
  "api_version": "v1", "request_id": "req-9",
  "job_id": "job_…", "result_id": "res_…",
  "status": "PROCESSING",
  "status_url": "/v1/jobs/job_…",
  "result_url": "/v1/results/res_…",
  "created_at": "2026-09-26T12:00:00+00:00"
}
```

`202` means **admitted** — never that the result will be VERIFIED.
Requires the durable store (`PLATRIXA_METERING_DATABASE_URL`); otherwise
`400 ASYNC_NOT_CONFIGURED`.

## 8. GET /v1/jobs/{job_id} — poll status

```bash
curl -s "$HOST/v1/jobs/job_…" -H "X-Platrixa-API-Key: YOUR_API_KEY"
```

```json
{
  "job_id": "job_…", "request_id": "req-9",
  "status": "REVIEW_REQUIRED",
  "retryable": false, "reason_codes": [],
  "result_url": "/v1/results/res_…",
  "created_at": "…", "updated_at": "…"
}
```

`status` is `PROCESSING` while running, then the real engine outcome or
`FAILED`. **Completion is not VERIFIED** — completion only means
processing finished. Poll until `status != "PROCESSING"`, then fetch the
result.

## 9. GET /v1/results/{result_id} — fetch the result

```bash
curl -s "$HOST/v1/results/res_…" -H "X-Platrixa-API-Key: YOUR_API_KEY"
```

Returns the **same canonical 5D envelope** as synchronous
`/v1/process` — async and sync results converge on one contract.
Unknown id → `404 RESULT_NOT_FOUND`; not complete yet →
`404 RESULT_NOT_READY` (keep polling the job).

## 10. GET /v1/capabilities — discovery

```bash
curl -s "$HOST/v1/capabilities" -H "X-Platrixa-API-Key: YOUR_API_KEY"
```

Read-only, deterministic listing of what the runtime can currently prove
and execute, derived live from the capability registry (with
`SUPPORTED`/`PARTIAL`/`UNSUPPORTED`/`PLANNED` metadata per capability).
Do not hardcode capability assumptions — discover them.

## 11. Idempotency-Key

Optional on `POST /v1/process` and `POST /v1/documents` (16–200 chars of
`[A-Za-z0-9._~-]`):

- same key + same request → the original response is replayed
  (`Idempotent-Replayed: true`, original `request_id` and HTTP status)
  **without consuming additional quota**;
- same key + different request → `409 IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST`;
- while the first attempt is still running → in-progress acknowledgement
  (`reason_code IDEMPOTENCY_REQUEST_IN_PROGRESS`, `Retry-After: 2`);
- deterministic failures (`INPUT_INVALID`) are stored and replayed;
  transient failures (`PROVIDER_UNAVAILABLE`) release the claim so
  retries genuinely retry;
- records age out after a 72-hour replayable window;
- zero-config deployments (no durable store) reject keys honestly with
  `400 IDEMPOTENCY_NOT_CONFIGURED`.

**No exactly-once promise** — this is durable idempotent *replay*.

## 12. Result contract

One envelope for sync and async results. Key fields:

| Field | Meaning |
|---|---|
| `status` | engine terminal state, verbatim (authoritative) |
| `api_status` | six-state public mapping: `PROCESSING` / `VERIFIED` / `REVIEW_REQUIRED` / `UNSUPPORTED` / `INVALID_INPUT` / `FAILED` |
| `engine_status` | verbatim engine state again (explicit) |
| `success` | `true` only for VERIFIED |
| `retryable` | advisory; true only for PROCESSING |
| `reason_code` / `reason_codes` | stable machine-readable reasons |
| `interpretation` | the model's schema-validated suggestion (`amounts[].value_origin: "EXTRACTED"`) |
| `accounting` / `accounting_result` | deterministic authority output (same value; null when no authority ran) |
| `evidence` | document evidence refs (page, bbox, confidence — null when the engine had none; never fabricated) |
| `lineage` | semantic field → supporting evidence ids |
| `metadata` | `engine_status`, `processing_time_ms`, `timings_ms`, `notes` |

Status semantics:

- **VERIFIED** — implemented schema + grounding + capability +
  deterministic-authority requirements satisfied. *Not* legal/tax
  compliance or advice.
- **REVIEW_REQUIRED** — possibly understandable but insufficient
  evidence/capability; queue for human review; never auto-post.
- **UNSUPPORTED** — outside the supported boundary; do not retry.
- **INVALID_INPUT** — the request itself is invalid.
- **FAILED** — deterministic rejection (`reason_codes`,
  `issues`, `grounding_issues`) or unexpected server failure.
- **PROCESSING** — admitted, result not yet available; retry/poll.

**Derived vs extracted:** never treat a model-extracted amount as
deterministic — the deterministic value lives only in
`accounting_result`, and only VERIFIED results carry one.

## 13. Evidence

For document inputs, `evidence` lists deterministic source citations:

```json
{
  "evidence_id": "doc_invoice_p1_e0003",
  "document_id": "doc_invoice_ab12cd34",
  "page": 1,
  "text": "Total: Rs. 15,000",
  "bbox": [12.0, 300.5, 200.0, 318.25],
  "extraction_confidence": 0.98,
  "source_type": "pypdf"
}
```

`bbox`/`extraction_confidence` are `null` when the engine did not provide
them (plain-text documents never have them). Text-only results carry
`evidence: []`. Audit `evidence` + `lineage` before trusting amounts.

## 14. Reason codes

`NO_SUPPORTED_CAPABILITY` · `SAFETY_BOUNDARY` · `FORBIDDEN_STRUCTURE` ·
`VALIDATION_REJECTED` · `GROUNDING_REJECTED` · `RESULT_PENDING` ·
`EVIDENCE_RECORDED` · `INPUT_INVALID` · `REQUEST_MALFORMED` ·
`UNAUTHORIZED` · `QUOTA_EXHAUSTED` · `METERING_UNAVAILABLE` ·
`PROVIDER_UNAVAILABLE` · `IDEMPOTENCY_KEY_INVALID` ·
`IDEMPOTENCY_KEY_TOO_LONG` ·
`IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST` ·
`IDEMPOTENCY_REQUEST_IN_PROGRESS` · `IDEMPOTENCY_NOT_CONFIGURED` ·
`IDEMPOTENCY_UNAVAILABLE` · `ASYNC_NOT_CONFIGURED` · `ASYNC_UNAVAILABLE` ·
`JOB_NOT_FOUND` · `RESULT_NOT_FOUND` · `RESULT_NOT_READY`.

Raw exception messages are never the machine contract.

## 15. Webhooks

`POST /v1/webhook-endpoints` registers an https URL with a subscription
list (`document.processing|completed|review_required|unsupported|failed`).
The signing secret is returned **exactly once**. Payloads are signed:

```
Platrixa-Signature: t=<unix>,v1=<hex hmac-sha256 over "{t}.{body}">
```

Verify with a ±300 s timestamp window; deduplicate on the deterministic
`id` field. **Delivery is best-effort single-attempt — not
at-least-once.** Polling `GET /v1/jobs/{job_id}` is the reliable path.

## 16. Error handling

```json
{
  "api_version": "v1",
  "error": {
    "code": "INPUT_INVALID",
    "message": "…",
    "request_id": "req-9",
    "api_status": "INVALID_INPUT",
    "api_status_label": "Invalid input",
    "retryable": false
  }
}
```

No stack traces, no internals, no raw exception messages — codes only.

## 17. Retry behavior

| Situation | Action |
|---|---|
| `PROVIDER_UNAVAILABLE` (503, retryable) | back off, retry; idempotency claim is released so a retry retries |
| `MODEL_UNAVAILABLE` → `api_status PROCESSING` | outcome unknown; retry or poll |
| `METERING_UNAVAILABLE` / `ASYNC_UNAVAILABLE` / `IDEMPOTENCY_UNAVAILABLE` (503) | fail-closed outage; retry later |
| `INPUT_INVALID` / 4xx | fix the request; retrying unchanged fails again |
| `429 QUOTA_EXHAUSTED` | wait for the monthly bucket |
| FAILED async job with `retryable: true` | resubmit the same document |

## 18. Rate / quota behavior

One unit per admitted request (sync and async alike). Monthly UTC
`YYYY-MM` buckets, atomic reservation, automatic rollover. There is no
application-level rate limiting — a 429 means the monthly quota is
exhausted, not "slow down this second".

## 19. Trust & Safety

Platrixa is a financial semantic validation and developer infrastructure
layer. It is **not** a financial/investment/tax/legal adviser, an
autonomous accountant, or a regulatory authority. A VERIFIED result does
not itself establish legal, tax, regulatory, or factual compliance.
When sufficient evidence/capability cannot be established, Platrixa
returns REVIEW_REQUIRED or UNSUPPORTED rather than inventing a
conclusion. Your application remains responsible for human review of
REVIEW_REQUIRED outcomes and for its own posting/business workflow.

## 20. Known limitations

- API keys are operator-provisioned; no self-serve issuance/rotation yet.
- Async documents and webhooks require the durable metering store;
  webhook delivery is best-effort single-attempt.
- The evaluated transaction domain is school-level (FYJC) accounting
  language — an early evaluation slice, not the product's definition;
  broader financial-document semantics are under evaluation.
- Idempotent replay covers a 72-hour window; no exactly-once execution.
- `X-Request-Id` is best-effort correlation, not idempotency.
- The API is a transport boundary — no rule packs, hooks, or provider
  configuration through it.

See [`HOSTED_API.md`](HOSTED_API.md) for the full normative contract.
