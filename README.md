# Platrixa — Financial & Accounting Intelligence Platform

**Stop prompts guessing math.** Platrixa puts a deterministic financial
runtime behind the model: an LLM interprets the student's transaction,
then grounding and deterministic accounting rules — not the model — decide
the answer, with evidence for every decision. First proving ground:
FYJC / Class 11 commerce bookkeeping.

## Hosted developer API (v1)

The hosted API is a **transport boundary** over the deterministic runtime:
malformed requests fail closed before any model or accounting code runs,
the model only ever *suggests*, and the runtime alone decides the final
state (`VERIFIED` / `REVIEW_REQUIRED` / `BLOCKED` / …).

Quickstart (run the server locally with
`uvicorn api.main:app --host 127.0.0.1 --port 8000`):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/process \
  -H "Content-Type: application/json" \
  -H "X-Platrixa-API-Key: $PLATRIXA_DEV_API_KEY" \
  -d '{"raw_input": "Purchased furniture for cash ₹15,000"}'
```

Actual response shape (fields abridged):

```json
{
  "api_version": "v1",
  "status": "VERIFIED",
  "success": true,
  "interpretation": {
    "transaction_type_enum": "PURCHASE",
    "amounts": [{"value": "15000", "source": "explicit"}],
    "suggested_status": "REVIEW_REQUIRED"
  },
  "accounting": {
    "debit_lines":  [{"account": "Furniture", "amount": 15000}],
    "credit_lines": [{"account": "Cash", "amount": 15000}]
  }
}
```

The model's suggestion (`REVIEW_REQUIRED`) and the final state
(`VERIFIED`) are deliberately different objects: interpretation is the
model's, the state is the runtime's.

Fail-closed demonstration — a malformed request never reaches the
runtime:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/process \
  -H "Content-Type: application/json" \
  -d '{broken json'
```

```json
{
  "api_version": "v1",
  "error": {"code": "REQUEST_MALFORMED",
             "message": "request body could not be parsed as a valid process request",
             "fields": [{"field": "", "reason": "json_invalid"}]}
}
```

Authentication: open by default for local development; when the server
sets `PLATRIXA_DEV_API_KEY`, requests must send that exact value in the
`X-Platrixa-API-Key` header (401 otherwise, before any processing).
Per-developer API keys with atomic monthly quota metering are also
supported (Phase 16): setting `PLATRIXA_METERING_DATABASE_URL` (PostgreSQL;
run `python -m backend.auth.init_metering` once to create the table)
activates the metered gate — each request must then present a valid
per-tenant key (401), within its monthly quota (429 `QUOTA_EXHAUSTED`),
with fail-closed 503 when the metering store is unavailable. There is no
billing or automated key issuance yet — see docs/HOSTED_API.md for the
full contract, states, error table, metering semantics, provider
configuration, rule-pack configuration, and current limitations.

## Project Identity

**Platrixa** is the financial/accounting reasoning platform being developed in this repository.

Its current architecture includes:
- Semantic compilation of financial transactions
- Curriculum-aware normalization
- Deterministic accounting reasoning (double-entry, single-entry, bill books)
- Settlement resolution (multi-payment, fraction-based, verbal amounts)
- GST handling (CGST/SGST/IGST)
- Multi-payment resolution (cash, bank, NEFT, cheque, fractions)
- Contradiction detection and safety/closure gates
- Provenance tracking
- Trusted curricular knowledge resolution
- Student-facing pedagogical projection

The next architectural expansion is: **stateful multi-transaction accounting processing**.

---

## Modular Architecture Migration

This project is being migrated from a single 1,300-line `app.py` into the
modular architecture below, **one module at a time**, with every existing
feature preserved exactly. See the bottom of this file for the rule this
migration follows.

### Target architecture

```
Platrixa/
    core/          ✅ DONE (this delivery)
    ingestion/     ⏳ not started
    gateway/       ⏳ not started
    timeline/      ⏳ not started
    intelligence/  ⏳ not started
    memo/          ⏳ not started
    exports/       ⏳ not started
    backend/       ⏳ not started
    frontend/      ⏳ not started
    tests/         🔶 started (core only so far)
```

## Status: Module 1 — `core/` ✅

**What was built:**
- `core/exceptions.py` — full custom exception hierarchy (`ProviderError`,
  `DocumentParsingError`, `ResponseValidationError`, `ExportGenerationError`,
  etc.), ready for `gateway/`, `ingestion/`, and `exports/` to raise instead
  of bare `ValueError`/`RuntimeError`.
- `core/config.py` — `EngineSettings` (typed, immutable config: model IDs,
  timeouts, retry policy, chunk sizes) + a `SecretsProvider` abstraction
  (`StreamlitSecretsProvider`, `EnvSecretsProvider`) so secrets can come
  from Streamlit today and environment variables / a secrets manager in
  the future backend, without other code changing.
- `core/constants.py` — `GROUNDING_RULE`, `DEFAULT_SESSION_STATE`, `ERROR_RESPONSE_MARKERS`.
- `core/logging.py` — standard Python logging setup + `ProviderEventLogger`
  with an injectable sink (`StreamlitSessionLogSink` today, `InMemoryLogSink`
  for tests/backend later) + `get_provider_health()`.
- `core/utilities.py` — `hash_text`, `CacheManager` (generic get-or-compute
  cache over any mutable mapping), `retry` (retry-with-backoff).
- `core/validation.py` — `is_error_response`, `contains_error_marker`,
  `extract_json` (robust JSON-from-AI-response parsing).

## Migration rule (applies to every future module)

1. Build one production module.
2. Integrate it into `app.py` (replace the corresponding inline code with
   imports; keep every existing name/behavior working).
3. Verify compatibility (compile check + unit tests).
4. Stop and wait for confirmation before starting the next module.

## Suggested next module

`gateway/` — Provider Manager, Router, Retry Engine, Circuit Breaker, Model
Selector. This absorbs Section 3 of `app.py` (`call_google_ai_studio`,
`call_groq_engine`, `_openrouter_request`, `call_openrouter_engine`,
`call_ai_with_fallback`), adds a real circuit breaker + cooldown (currently
missing — only retry-with-backoff exists today), and is what eventually
lets you plug in your own AI Gateway alongside Google/Groq/OpenRouter.
