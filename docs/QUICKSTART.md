# Platrixa Quickstart

From zero to a validated financial-semantic result in five calls.

> All examples use placeholders (`YOUR_API_KEY`, `YOUR_IDEMPOTENCY_KEY`) —
> never real secrets. Keys are provisioned by the operator (`python -m
> backend.auth.dev_seed_tenant`); there is no self-serve signup yet.

---

## 0. Base URL

```bash
HOST=http://127.0.0.1:8000
# local server: python -m uvicorn api.main:app --port 8000
```

## 1. Obtain an API key

Ask the operator (or provision locally):

```bash
PLATRIXA_METERING_DATABASE_URL=postgresql://… \
  python -m backend.auth.dev_seed_tenant --tenant acme-dev --limit 2000
```

The raw key (`plx_…`) is printed **exactly once**. Store it securely.

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

**Trust & safety:** Platrixa is financial semantic validation
infrastructure, not a financial/investment/tax/legal adviser and not a
regulatory authority. A VERIFIED result does not establish compliance.
See `docs/DEVELOPER_GUIDE.md` §19 and `docs/HOSTED_API.md` for the full
contract and known limitations.
