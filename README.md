# Financial Timeline Engine â€” Modular Architecture Migration

This project is being migrated from a single 1,300-line `app.py` into the
modular architecture below, **one module at a time**, with every existing
feature preserved exactly. See the bottom of this file for the rule this
migration follows.

## Target architecture

```
FinancialTimelineEngine/
    core/          âœ… DONE (this delivery)
    ingestion/     â³ not started
    gateway/       â³ not started
    timeline/      â³ not started
    intelligence/  â³ not started
    memo/          â³ not started
    exports/       â³ not started
    backend/       â³ not started
    frontend/      â³ not started
    tests/         ðŸ”¶ started (core only so far)
```

## Status: Module 1 â€” `core/` âœ…

**What was built:**
- `core/exceptions.py` â€” full custom exception hierarchy (`ProviderError`,
  `DocumentParsingError`, `ResponseValidationError`, `ExportGenerationError`,
  etc.), ready for `gateway/`, `ingestion/`, and `exports/` to raise instead
  of bare `ValueError`/`RuntimeError`.
- `core/config.py` â€” `EngineSettings` (typed, immutable config: model IDs,
  timeouts, retry policy, chunk sizes) + a `SecretsProvider` abstraction
  (`StreamlitSecretsProvider`, `EnvSecretsProvider`) so secrets can come
  from Streamlit today and environment variables / a secrets manager in
  the future backend, without other code changing.
- `core/constants.py` â€” `GROUNDING_RULE`, `DEFAULT_SESSION_STATE`,
  `ERROR_RESPONSE_MARKERS`.
- `core/logging.py` â€” standard Python logging setup + `ProviderEventLogger`
  with an injectable sink (`StreamlitSessionLogSink` today, `InMemoryLogSink`
  for tests/backend later) + `get_provider_health()`.
- `core/utilities.py` â€” `hash_text`, `CacheManager` (generic get-or-compute
  cache over any mutable mapping), `retry` (retry-with-backoff).
- `core/validation.py` â€” `is_error_response`, `contains_error_marker`,
  `extract_json` (robust JSON-from-AI-response parsing).

**Integration into `app.py`:**
`app.py`'s old Section 1 (config/session-state/logging/utilities/retry) and
the old inline JSON-extraction helper in Section 5 now import from `core`
instead of defining everything locally. Every existing name
(`PRIMARY_MODEL`, `CHUNK_SIZE`, `_hash_text`, `_cached_call`,
`_log_provider_event`, `get_provider_health`, `_retry`,
`_extract_json_from_ai_response`, `GROUNDING_RULE`, `FUTURE_MODULES`) is
still present and behaves identically â€” they're now thin aliases over the
`core` implementations, so **zero call sites elsewhere in `app.py` needed
to change**. One pre-existing dead import (`import re`, never used in the
original file) was removed while touching the import block.

**Deliberately left untouched this pass** (so the diff stays scoped to
`core/`): the three provider-calling functions
(`call_google_ai_studio`/`call_groq_engine`/`_openrouter_request`) still
call `st.secrets.get(...)` directly, and `check_login()` still hard-codes
its demo credentials. Both will move onto `core.config.get_secret` /
`core.config.SETTINGS` when `gateway/` and `backend/authentication` are
built respectively â€” moving them now would touch code that isn't part of
this module.

**Verification performed:**
- `core/` has no import-time dependency on `streamlit` (only inside two
  small classes' methods) â€” confirmed by importing `core` standalone.
- `python -m py_compile` on `app.py` and every `core/*.py` file.
- `tests/test_core.py` â€” 12 unit tests, all passing, covering hashing,
  caching, retry (success-after-failure and exhaustion), JSON extraction
  (fenced/prose-wrapped/error/array), error-marker detection, injectable
  provider health, injectable provider event logging, and that
  `EngineSettings` defaults exactly match the original hard-coded values.
  (Streamlit itself isn't installed in this sandbox, so a live
  `streamlit run app.py` smoke test could not be performed here â€” please
  run it in your environment before deploying.)

## Migration rule (applies to every future module)

1. Build one production module.
2. Integrate it into `app.py` (replace the corresponding inline code with
   imports; keep every existing name/behavior working).
3. Verify compatibility (compile check + unit tests).
4. Stop and wait for confirmation before starting the next module.

## Suggested next module

`gateway/` â€” Provider Manager, Router, Retry Engine, Circuit Breaker, Model
Selector. This absorbs Section 3 of `app.py` (`call_google_ai_studio`,
`call_groq_engine`, `_openrouter_request`, `call_openrouter_engine`,
`call_ai_with_fallback`), adds a real circuit breaker + cooldown (currently
missing â€” only retry-with-backoff exists today), and is what eventually
lets you plug in your own AI Gateway alongside Google/Groq/OpenRouter.
