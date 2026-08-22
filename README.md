# Platrixa — Financial & Accounting Intelligence Platform

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
