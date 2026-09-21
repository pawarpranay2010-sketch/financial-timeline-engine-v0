---
title: Platrixa — Financial Semantics Specialist
emoji: 🧮
colorFrom: yellow
colorTo: purple
sdk: gradio
sdk_version: 5.49.1
app_file: app.py
pinned: false
suggested_hardware: zero-a10g
---

# Platrixa — Financial Semantics Specialist

Deterministic financial semantic validation infrastructure for AI-powered
accounting and finance software. **AI interprets. Deterministic authorities
decide and execute.**

This Space serves the **exact Phase 6C model artifact** — a historical,
FYJC-focused development/evaluation slice of Platrixa's broader
financial-semantic scope, not the definition of the product:

| Artifact | Value |
|---|---|
| Base model | `Qwen/Qwen2.5-1.5B-Instruct` |
| Base revision | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| Adapter | `Pranay-20/platrixa-fyjc-specialist-v0.1` |
| Adapter revision | `b5c0a37cebc00e93144150dbbcaa7b28cadb259e` |
| Decoding | greedy (temperature = 0.0), max_new_tokens = 1024 |
| Prompt format | byte-exact Alpaca SFT template used in Phase 6B training |

## What this Space does

It is a **language-understanding specialist only**. Given a financial
accounting sentence, it returns the model's structured interpretation as the
18-field ExpandedInterpretation contract (transaction_type, parties, amounts,
payment_method, references, ambiguities, grounding, plus the 11 expanded
fields).

It **never** produces journal entries, debit/credit decisions, ledger
postings, trial balances, or any accounting conclusion. The deterministic
Platrixa Kernel remains the sole authority for accounting truth.

## API

Raw HTTP (RemoteHFModelProvider-compatible):

```
GET  /health
     200 {"status": "healthy", "adapter_loaded": true, ...runtime versions}
     503 when the pinned model/adapter is not loaded (fail-closed)

POST /interpret
     body: {"text": "Purchased furniture for cash Rs. 15,000"}
     200 {"interpretation": {<18 fields>}, "model": {<identity metadata>}}
     422 {"error": "malformed_output" | "forbidden_accounting_fields", ...}
     503 {"error": "model_unavailable", ...}
```

Gradio named API: `interpret_core` (same response envelope).

## Fail-closed behavior

- Adapter load failure ⇒ the model is dropped entirely; **no base-only
  fallback**, no other model, no keyword parsing.
- Malformed model output ⇒ explicit `malformed_output` error.
- Missing contract fields ⇒ explicit validation error.
- Forbidden accounting fields in output ⇒ explicit rejection.
- Model/adapter unavailable ⇒ HTTP 503 with identity metadata.

## Status (verified live 2026-09-09)

- ✅ Space builds and runs (ZeroGPU, stage RUNNING)
- ✅ Base model loaded from the exact pinned revision
- ✅ LoRA adapter loaded from the exact pinned revision (fail-closed)
- ✅ Actual generation verified via the named API (greedy decoding)
- ✅ 18-field contract returned on live requests (T1–T5, T8b)
- ✅ Ambiguity represented, not invented away (T4, T8b → REVIEW_REQUIRED)
- ✅ No accounting leakage; injection attempt (T7) produced no journal/debit/credit fields
- ✅ Empty input → explicit malformed_output error envelope (T8a)
- ✅ Model identity metadata reports both locked revisions

Not claimed production-ready: single-replica demo scale; latency and
concurrency under sustained load are unmeasured. The FYJC-style accounting
language this artifact was trained on is Platrixa's early development and
evaluation slice; Platrixa's overall product scope is broader financial
semantic validation infrastructure.
