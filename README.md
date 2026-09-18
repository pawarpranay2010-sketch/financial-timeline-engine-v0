# Platrixa

**Financial semantics infrastructure for developers.**

Send financial language; receive structured, validated, evidence-grounded
accounting semantics — with explicit `VERIFIED`, `REVIEW_REQUIRED`, or
`BLOCKED` states instead of confident guesses.

## What Platrixa does

```
input (transaction text)
   ↓
semantic interpretation (LLM — suggests only)
   ↓
CandidateSemanticIR (18-field structured contract)
   ↓
schema verification (strict, fail-closed)
   ↓
grounding / evidence validation (fail-closed)
   ↓
deterministic accounting kernel
   ↓
VERIFIED / REVIEW_REQUIRED / BLOCKED  →  JSON result
```

**LLM ≠ authority.** The model interprets financial language; Platrixa
deterministically validates, grounds, and decides the final accounting
result. Every output carries its evidence; anything the runtime cannot
prove is downgraded to `REVIEW_REQUIRED` or `BLOCKED` — never guessed.
Transaction-level deterministic reasoning includes multi-payment
settlement (cash/bank/cheque/NEFT/fractions), GST handling, and
contradiction detection.

## What works today

| Capability | Status |
|---|---|
| Hosted `POST /v1/process` (versioned developer API) | ✅ shipped |
| API-key authentication (fail-closed 401) | ✅ shipped |
| Unique per-tenant API keys (CSPRNG, hash-only storage) | ✅ shipped |
| Tenant monthly quotas with atomic reservation | ✅ shipped |
| `GET /v1/health` · `GET /v1/ready` | ✅ shipped |
| Python library (`from platrixa import Platrixa`) | ✅ shipped |
| CLI (`python -m platrixa process`) | ✅ shipped |
| Schema verification + grounding + deterministic kernel | ✅ shipped |
| Server-side rule packs / hooks (downgrade-only) | ✅ shipped |
| Self-serve signup / billing automation / dashboard | ❌ not yet |
| Key-rotation UI, idempotency, app-level rate limiting | ❌ not yet |
| Bank statements / invoices / document understanding | ⏳ under evaluation |

## How developers use Platrixa

### 1. Hosted API (primary path)

```bash
curl -s -X POST https://<your-platrixa-host>/v1/process \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: $PLATRIXA_API_KEY" \
  -d '{"raw_input": "Paid ₹12,500 to Raj for office furniture by cheque."}'
```

Response shape (fields abridged; the runtime's state is authoritative):

```json
{
  "api_version": "v1",
  "request_id": "…",
  "status": "VERIFIED",
  "status_label": "Verified",
  "success": true,
  "next_action": null,
  "issues": [],
  "grounding_issues": [],
  "rule_evidence": [],
  "interpretation": {
    "transaction_type_enum": "PURCHASE",
    "parties": ["Raj"],
    "amounts": [{"value": "12500", "source": "explicit"}]
  },
  "accounting": {
    "debit_lines":  [{"account": "Furniture", "amount": 12500}],
    "credit_lines": [{"account": "Bank", "amount": 12500}]
  }
}
```

`interpretation` is the model's suggestion; `status` is decided by the
deterministic runtime and is the only field your integration should act on.

Transport rules: malformed JSON → `400 REQUEST_MALFORMED`; invalid domain
input → `422 INPUT_INVALID`; missing/invalid key → `401 UNAUTHORIZED`;
exhausted quota → `429 QUOTA_EXHAUSTED`; metering store unavailable →
`503 METERING_UNAVAILABLE` (fail-closed — never admitted). See
`docs/HOSTED_API.md` for the full contract.

### API keys

API keys are currently **provisioned manually by the operator** (there is
no self-serve signup yet). Every tenant receives a **unique**
cryptographically random key (`plx_…`):

```bash
# 1. create the quota table (once, idempotent)
PLATRIXA_METERING_DATABASE_URL=postgresql://… python -m backend.auth.init_metering

# 2. provision one tenant — the raw key is printed EXACTLY ONCE
PLATRIXA_METERING_DATABASE_URL=postgresql://… \
  python -m backend.auth.dev_seed_tenant --tenant acme-dev --limit 2000
```

The developer sends the key on every call:

```
X-Platrixa-API-Key: <their-key>
```

**Storage:** the server stores only the SHA-256 hash of the key — never
the plaintext. The raw key is not recoverable from the database and is
never logged or echoed. Treat the printed key like a password: it cannot
be re-displayed later (manual replacement = provision a new key).

### Metering

Each tenant has a monthly quota (`monthly_limit`) counted in UTC `YYYY-MM`
buckets. One unit is consumed per **admitted** request; authentication
failures consume zero. Quota reservation is a single atomic database
UPDATE, so concurrent requests can never exceed the limit. When exhausted:

```
HTTP/1.1 429 Too Many Requests
{"api_version": "v1", "error": {"code": "QUOTA_EXHAUSTED",
 "message": "monthly quota exhausted for this API key"}}
```

Month rollover resets usage automatically inside the reservation (no
scheduler, no cross-month leakage).

### 2. Python (local library)

```python
from platrixa import Platrixa

p = Platrixa()
result = p.process("Paid ₹12,500 to Raj for office furniture by cheque.")
print(result.status)          # VERIFIED / REVIEW_REQUIRED / BLOCKED …
print(result.interpretation)  # 18-field structured interpretation
print(result.accounting)      # deterministic accounting result
print(result.to_dict())       # JSON-safe projection
```

The Python library runs the same deterministic pipeline locally and is a
separate surface from the hosted API.

### 3. CLI

```bash
python -m platrixa process "Paid ₹12,500 to Raj for office furniture by cheque."
python -m platrixa --version
```

## Do I need to download the model?

**No — not for the hosted API.** The model is an internal implementation
detail behind the semantic pipeline; hosted-API developers never download
or manage model weights.

Local execution via the Python library is supported for development but
requires installing the model dependencies and letting the provider load
weights on first use (`provider="auto"` resolves the configured provider).
Direct model download is not the documented integration path.

## Current limitations

NOT currently available:

- self-serve signup, billing automation, payment webhooks
- self-serve dashboard or API-key rotation UI
- application-level rate limiting, idempotency keys, exactly-once
  request deduplication (monthly quota metering is not a rate limiter)
- enterprise SLA guarantees or uptime commitments
- consumer financial advice — Platrixa produces accounting semantics,
  not investment advice, by design

Breadth: Platrixa's proven domain is FYJC-style transaction language.
Bank narration, invoice, and broader financial-document support are under
evaluation — do not treat them as supported until the Phase 24 foundation
evaluation and subsequent validation establish evidence.

## Roadmap (short)

1. Manual-provisioning maturity: usage endpoint + operator tooling.
2. Billing: payment → automated tenant provisioning (webhook).
3. Self-serve dashboard with key rotation.
4. Breadth expansion gated on evaluation evidence.

Historical phase reports and research live in `reports/`, `docs/`, and
`training/` — the root README reflects only the current product.

## Hosting & deployment

The service is a long-running FastAPI app (`uvicorn api.main:app`), reads
`PORT`, and needs PostgreSQL only when metering is enabled
(`PLATRIXA_METERING_DATABASE_URL`) or for the application datastore
(`DATABASE_URL`). Model/provider credentials are server-side environment
secrets and are never exposed through the API. See `DEPLOYMENT.md` and
`docs/HOSTED_API.md` for the complete operational contract.
