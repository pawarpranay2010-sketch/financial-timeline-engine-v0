# Platrixa Hosted Developer API (`/v1`) — Phase 13

The API is a **transport boundary** over the Platrixa runtime. It contains
no accounting logic, no grounding, no rules, and no status authority —
every request flows through the public developer interface into the
Kernel, which remains the single execution authority:

```
Developer
   ↓  HTTPS
Platrixa API  (/v1/*)
   ↓  request-shape validation
Public developer interface  (platrixa.Platrixa)
   ↓  exactly one call
Kernel.process(...)
   ↓
Provider → Interpretation → Grounding → Accounting → Rules
   ↓
Stable result + evidence
```

## Endpoint & version

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/process` | Process one financial transaction (text) |
| `POST` | `/v1/process/document` | Process a transaction from **text, a PDF, or an image** |
| `GET` | `/v1/health` | Liveness — API process alive (touches nothing) |
| `GET` | `/v1/ready` | Readiness — dependencies available enough to process |

Version: `v1`, reported in every success response as `"api_version": "v1"`.
The browser-facing `/api/v1/*` routes are unchanged.

## Local startup

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

or `python3 -m uvicorn api.main:app --port 8000`. No provider keys,
database, or model download are needed to boot; the model loads lazily on
first processing request (or per your provider configuration).

## Authentication (server-side)

Two independent, stackable gates protect the endpoint:

**1. Shared server key (zero-config).** Open by default (zero-config local
development). To gate it, the server operator sets `PLATRIXA_DEV_API_KEY`;
requests must then send the exact value in the `X-Platrixa-API-Key` header.
Missing, empty, or invalid keys are rejected with **401** *before any
processing* (the key is compared in constant time and never logged, echoed,
or serialized). Developers cannot supply rule packs or Python hooks — the
request schema carries only `raw_input`.

**2. Metered developer gate (per-key, per-tenant quota — Phase 16).** When
the server sets `PLATRIXA_METERING_DATABASE_URL` (a PostgreSQL URL, after
running `python -m backend.auth.init_metering` to create the table), each
request must present a per-developer API key in the same
`X-Platrixa-API-Key` header. The gate hashes the key (SHA-256), resolves
the tenant, and atomically reserves one unit of that tenant's monthly
quota **before** the public interface is resolved — a rejected request
never loads the model:

| Condition | HTTP | Error code |
|---|---|---|
| missing / unknown / deactivated key | 401 | `UNAUTHORIZED` (all three externally indistinguishable) |
| monthly quota exhausted | 429 | `QUOTA_EXHAUSTED` |
| metering store unavailable | 503 | `METERING_UNAVAILABLE` (fail closed — never admitted) |

Quota semantics: `YYYY-MM` UTC buckets (a stale bucket rolls over inside
the same atomic reservation — no scheduler, and August usage is never
counted as September usage); authentication and quota failures consume
zero units; one unit is consumed per **admitted** request, including
requests that later fail domain validation (the unit paid for admission).
Raw keys are never stored (hash only) and never appear in responses,
logs, or error bodies. Concurrency safety is enforced by the database: a
single `UPDATE` whose `WHERE` clause carries the full admission predicate,
so concurrent admissions can never exceed the monthly limit.

Tenants are provisioned by the operator (dev/test helper:
`python -m backend.auth.dev_seed_tenant --tenant <id> --limit <n>`, which
prints the raw key exactly once). When the metering variable is unset,
gate 2 is absent entirely — gate 1 behavior is unchanged (there is no
fallback to `DATABASE_URL`; metering activation is explicit).

## Request

`POST /v1/process` with a JSON body using the existing canonical input
contract (raw transaction text, 1–2000 characters):

```json
{ "raw_input": "Purchased furniture for cash ₹15,000" }
```

Optional header: `X-Request-Id` (a correlation id matching
`[A-Za-z0-9._-]{1,128}`; echoed on the response when well-formed, ignored
otherwise).

## Response

The `status` field carries the Kernel's terminal state **verbatim** —
the HTTP layer can never create or upgrade a state:

| State | Meaning | HTTP |
|---|---|---|
| `VERIFIED` | deterministic accounting passed | 200 |
| `REVIEW_REQUIRED` | valid but flagged for review (or downgraded by a rule) | 200 |
| `BLOCKED` | rejected by the safety boundary | 200 |
| `VALIDATION_FAILED` / `GROUNDING_FAILED` / `FORBIDDEN_OUTPUT` / `UNSUPPORTED_TRANSACTION` | processing rejected, reason in `issues` | 422 |
| `MODEL_UNAVAILABLE` | provider unavailable — retry later | 503 |

(Each engine state also carries its six-state `api_status` mapping — see
"Public API status" below.)

`success` is `true` only for `VERIFIED`.

Example:

```json
{
  "api_version": "v1",
  "request_id": "bc-1",
  "status": "VERIFIED",
  "status_label": "Verified",
  "success": true,
  "next_action": null,
  "issues": [],
  "grounding_issues": [],
  "rule_evidence": [],
  "interpretation": {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "suggested_status": "REVIEW_REQUIRED"
  },
  "accounting": {
    "status": "VERIFIED",
    "journal_entries": [{"debit": "Furniture A/c", "credit": "Cash A/c", "amount": 25000}]
  }
}
```

(Fields are the Kernel's own result projection; interpretation/accounting
carry the schema-validated candidate and deterministic accounting result
minus internal model metadata.)

## Public API status (six states — Phase 5A)

Every success and error response carries `api_status`, a **transport-layer
mapping** of the outcome onto a closed six-state vocabulary. It is derived
deterministically from the engine's terminal state (or the transport error
code); the HTTP layer never creates, upgrades, or softens an outcome, and
the engine's own state is always carried beside it verbatim in
`engine_status` (or `status` for engine results), so no information is
lost by relabeling.

| `api_status` | Meaning | Retryable | Engine states |
|---|---|---|---|
| `PROCESSING` | admitted, authoritative result not available (provider/runtime unavailable) | yes | `MODEL_UNAVAILABLE` |
| `VERIFIED` | deterministic execution passed | no | `VERIFIED` (only source) |
| `REVIEW_REQUIRED` | valid but flagged for human review (incl. rule downgrades) | no | `REVIEW_REQUIRED` |
| `UNSUPPORTED` | no deterministic authority accepted the input | no | `BLOCKED`, `UNSUPPORTED_TRANSACTION`, `FORBIDDEN_OUTPUT` |
| `INVALID_INPUT` | malformed transport request or input rejected by the public contract | no | (400/401/413/422 error paths) |
| `FAILED` | deterministic rejection with the reason in `issues`/`grounding_issues`, or unexpected server failure | no | `VALIDATION_FAILED`, `GROUNDING_FAILED`, unknown states (fail-closed) |

Contract guarantees:

- The vocabulary is closed (exactly these six values, never extended at
  runtime); an unmapped engine state fails closed to `FAILED`.
- No engine state maps to `VERIFIED` except `VERIFIED` itself.
- `retryable` is `true` only for `PROCESSING`; it is advisory.
- `reason_code` is a stable public name for recorded evidence on
  fail-closed states (`SAFETY_BOUNDARY`, `NO_SUPPORTED_CAPABILITY`,
  `FORBIDDEN_STRUCTURE`, `VALIDATION_REJECTED`, `GROUNDING_REJECTED`,
  `RESULT_PENDING`, `EVIDENCE_RECORDED`); `null` otherwise.
- `api_status_label` is a human-readable label for UIs. It carries no
  authority — integrate on `api_status`, never on the label.
- The mapping adds no accounting, grounding, or schema semantics: the
  engine terminal state remains the single status authority.

## Error responses

Machine-readable envelope; no stack traces, filesystem paths, or secrets:

```json
{
  "api_version": "v1",
  "error": {
    "code": "INPUT_INVALID",
    "message": "input must be a non-empty transaction string",
    "request_id": "bc-1",
    "api_status": "INVALID_INPUT",
    "api_status_label": "Invalid input",
    "retryable": false
  }
}
```

`error.api_status` maps the transport rejection onto the same six-state
contract (see above); `code` itself is unchanged and remains the
fine-grained machine-readable reason.

| Condition | Code | HTTP |
|---|---|---|
| Malformed request (invalid JSON, empty body, wrong content type, missing/wrong-typed fields) | `REQUEST_MALFORMED` | 400 |
| Empty/whitespace input, oversized text | `INPUT_INVALID` | 422 |
| Body over 64 KiB | `REQUEST_TOO_LARGE` | 413 |
| Missing/invalid API key (when configured) | `UNAUTHORIZED` | 401 |
| Provider runtime failure / unavailable | `PROVIDER_UNAVAILABLE` | 503 |
| Unexpected server error | `detail` (exception type name only) | 500 |

Malformed HTTP never reaches the runtime (Kernel invocation = 0) and the
400 body carries only field paths and error types — never raw input
values, stack traces, or filesystem paths. Domain-level rejections
(valid JSON rejected by the runtime's input contract) keep their
documented 422 `INPUT_INVALID` shape.

## Health vs readiness

- `GET /v1/health` — liveness only. Constructs nothing, calls nothing; can
  never trigger a model load.
- `GET /v1/ready` — reports provider readiness (via the interface's status
  view, which **never loads the model**; a cold-but-loadable provider is
  ready — the first request may pay the load cost) plus the configured
  rule pack summary. Dependency failures are reported with a reason, never
  hidden.

## Provider & server configuration

Provider selection uses the repository's existing environment mechanism —
no new variables:

- `PLATRIXA_MODEL_ENDPOINT_URL` set → remote provider; unset → local HF provider
- `PLATRIXA_MODEL_ENDPOINT_TOKEN`, `PLATRIXA_MODEL_TIMEOUT`,
  `PLATRIXA_MODEL_TRANSPORT=gradio` — remote connection settings
- `PLATRIXA_RULE_PACK_PATH` — **server-side** path to a trusted YAML rule
  pack (Phase 10 format). Loaded at startup of the first request; a
  malformed pack fails closed (503 / not_ready), never silently ignored.

## RulePack & RuleHook restrictions

- YAML rule packs: server configuration only (`PLATRIXA_RULE_PACK_PATH`).
- Python rule hooks: **server-side code only**. The request schema has no
  rule/hook field, so a client can never submit executable rule code.
- Rules can only **downgrade** a deterministic success state
  (`VERIFIED → REVIEW_REQUIRED/BLOCKED`). A hook requesting `VERIFIED` is
  sanitized and recorded in evidence. Only the Kernel can produce
  `VERIFIED`.

## Observability

Requests are logged as safe metadata only: endpoint, request id, duration,
resulting state, coarse error category. Financial content, credentials,
and secrets are never logged.

## Idempotency & duplicate requests

Duplicate submissions are **not** guaranteed to collapse into one
processing operation. The API performs no deduplication and no retries;
each accepted request runs the Kernel once and returns its own result.
Distributed exactly-once semantics are future work.

## Known limits (launch posture)

- Per-developer authentication and monthly quota metering are implemented
  (Phase 16, `PLATRIXA_METERING_DATABASE_URL`); billing and automated
  key issuance are not — tenants are provisioned by the operator.
- No application-level rate limiting; platform-level controls only.
- `X-Request-Id` is a best-effort correlation id, not an idempotency key.
- The API is a transport boundary: it cannot be used to alter rule
  packs, hooks, provider configuration, or the runtime's authority model.

## Hosting limitations (honest)

- The runtime depends on an external model provider. On the HF Space path,
  ZeroGPU quota and model cold starts can make requests slow (tens of
  seconds) or temporarily unavailable (`MODEL_UNAVAILABLE` → 503).
- Metering requires a reachable PostgreSQL store; when it is configured
  but unavailable the API fails closed (503) rather than admitting
  unmetered requests.
- The API is not deployed by default; run it locally with the command
  above.

## Client examples

curl:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/process \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: $PLATRIXA_DEV_API_KEY" \
  -H "X-Request-Id: my-req-1" \
  -d '{"raw_input": "Purchased furniture for cash ₹15,000"}'
```

(Omit the API-key header when the server has none configured.)

Python (HTTP only — no internal backend objects):

```python
import os

import requests

headers = {"X-Request-Id": "my-req-1"}
if os.getenv("PLATRIXA_DEV_API_KEY"):
    headers["X-Platrixa-API-Key"] = os.environ["PLATRIXA_DEV_API_KEY"]

resp = requests.post(
    "http://127.0.0.1:8000/v1/process",
    json={"raw_input": "Purchased furniture for cash ₹15,000"},
    headers=headers,
    timeout=120,
)
resp.raise_for_status()
result = resp.json()
print(result["status"], result["accounting"])
```

Runnable versions live in `examples/developer_interface/`
(`api_curl.sh`, `api_python.py`).

## Architecture boundary

`api/routes/developer.py` imports only the public interface
(`platrixa`) and the shared transport mapping — it never imports backend
internals (kernel, providers, accounting, grounding, rules, persistence)
and performs no persistence. See `PLATRIXA_PHASE13_HOSTED_API_REPORT.md`
for the evidence suite (`scripts/fte_fyjc_62_hosted_api_boundary_test.py`)
proving the routing and authority invariants.

---

# Document submissions (`POST /v1/process/document`)

Submit a **text, PDF, or image** and receive the same validated result you
get from `/v1/process`. You do not need to run your own OCR: Platrixa owns
ingestion, document understanding, evidence normalization, semantic
interpretation, schema verification, grounding, and authority routing.

## Request

Two mutually exclusive forms:

```bash
# 1. text (JSON)
curl -s -X POST https://<host>/v1/process/document \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  -d '{"raw_input": "Paid ₹12,500 to Raj for office furniture by cheque."}'

# 2. a document (multipart)
curl -s -X POST https://<host>/v1/process/document \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  -F "document=@invoice.pdf"
```

Accepted uploads: `.pdf`, `.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`,
`.webp`, `.txt`. Maximum **10 MiB** per document.

Providing both `raw_input` and a file — or neither — is rejected.

## Response

The financial part of the body is identical to `/v1/process`: `status` is
the runtime's own verdict, carried **verbatim**. The document path cannot
produce, upgrade, or soften it. On top of that, the response adds
provenance so a result can be audited:

```json
{
  "api_version": "v1",
  "status": "REVIEW_REQUIRED",
  "success": false,
  "document": {
    "document_id": "doc_invoice_1a2b3c4d5e6f7890",
    "page_count": 3,
    "pages_by_status": {"DIGITAL_TEXT": [1, 2], "IMAGE_ONLY": [3], "EXTRACTION_FAILED": []},
    "pages_needing_ocr": [3],
    "engine": "rapidocr",
    "engine_version": "1.3.24",
    "pages": [{"page": 3, "status": "IMAGE_ONLY", "text_chars": 0, "reason": "no_extractable_text_layer"}]
  },
  "evidence": [
    {
      "evidence_id": "doc_invoice_1a2b...:p3:e0000",
      "page": 3,
      "text": "Paid 12500 to Raj ...",
      "bbox": [60.0, 130.0, 900.0, 170.0],
      "extraction_confidence": 0.95,
      "source_type": "rapidocr:p3",
      "engine": "rapidocr",
      "engine_version": "1.3.24"
    }
  ],
  "timings_ms": {"document_understanding_ms": 41.2, "semantic_validation_pipeline_ms": 63.5},
  "notes": []
}
```

**Evidence lineage.** `evidence[]` carries the page, bounding box, text,
confidence and OCR engine behind the content, so you can answer *"which
page/region caused this fact?"* The `document_id` and `evidence_id` values
are content-addressed and deterministic: the same file always yields the
same IDs.

## What the document layer will never do

* OCR is **not** a financial authority. It reports what is on the page.
* It never computes totals, taxes, balances, or any arithmetic.
* It never emits `VERIFIED`, and it never bypasses schema verification or
  grounding.
* It never fabricates text, confidence, or evidence. A page that cannot be
  read stays `EXTRACTION_FAILED`; a region below the confidence floor is
  dropped rather than guessed.

## Failure behaviour (fail-closed)

| Situation | Result |
|---|---|
| Page has no text layer and no OCR engine installed | Page stays `IMAGE_ONLY`; no text is invented |
| Scanned/image document, no OCR available | Interpreter receives page markers only → `GROUNDING_FAILED` (measured) |
| OCR returns nothing usable | Page stays `IMAGE_ONLY`; `notes` explains why |
| OCR confidence below the floor (0.30) | Region excluded from evidence entirely |
| Corrupt / truncated file | Single `EXTRACTION_FAILED` page; no interpretation |
| Ambiguous document | The runtime's own refusal state (`BLOCKED` / `NOT_SUPPORTED` / `REVIEW_REQUIRED`) |

Transport errors are `400` (missing/ambiguous/empty input), `413` (too
large), `415` (unsupported type). They are **not** financial verdicts.

## Enabling OCR

OCR is **off by default** so the core install stays lightweight. To enable:

```bash
pip install -r requirements-ocr.txt
```

Platrixa then selects RapidOCR (PP-OCRv5, Apache-2.0) and falls back to
Tesseract, using them only for pages that have no text layer. Force a
choice with `PLATRIXA_OCR_ENGINE=rapidocr | tesseract | none`, and disable
entirely with `none`.

If no OCR engine is installed, text-based documents are unaffected and
scanned documents fail closed.

See `docs/PLATRIXA_DOCUMENT_UNDERSTANDING_PHASE_1_4_REPORT.md` for the full
architecture, measured benchmarks, licensing, and known gaps.
