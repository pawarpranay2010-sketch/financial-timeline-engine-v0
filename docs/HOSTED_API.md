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
| `POST` | `/v1/process` | Process one financial transaction |
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

## Error responses

Machine-readable envelope; no stack traces, filesystem paths, or secrets:

```json
{
  "api_version": "v1",
  "error": {
    "code": "INPUT_INVALID",
    "message": "input must be a non-empty transaction string",
    "request_id": "bc-1"
  }
}
```

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
