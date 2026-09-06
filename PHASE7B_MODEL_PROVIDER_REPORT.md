# PHASE 7B — MODEL PROVIDER BOUNDARY — FINAL REPORT

## Status

PHASE_7B_STATUS: PASS

ARCHITECTURE_AUDIT: PASS
MODEL_PROVIDER_INTERFACE: PASS
LOCAL_HF_PROVIDER: PASS
MODEL_RUNNER_REUSE: PASS
MODEL_REVISION_PINNING: PASS
ADAPTER_REVISION_PINNING: PASS
LAZY_LOADING: PASS
ERROR_HANDLING: PASS
ACCOUNTING_BOUNDARY: PASS
COMPATIBILITY: PASS
SECURITY: PASS
TESTS: PASS
PHASE_6C_FREEZE: PASS

## Artifacts

BASE_MODEL: Qwen/Qwen2.5-1.5B-Instruct
BASE_REVISION: 989aa7980e4cf806f80c7fef2b1adb7bc71aa306

ADAPTER_REPO: Pranay-20/platrixa-fyjc-specialist-v0.1
ADAPTER_REVISION: b5c0a37cebc00e93144150dbbcaa7b28cadb259e

## Changed files

- backend/model_provider/__init__.py
- backend/model_provider/base.py
- backend/model_provider/local_hf.py
- scripts/fte_fyjc_51_model_provider_test.py

No other files were modified. In particular, nothing in training/,
backend/maths/fyjc_contract.py, schema_verifier.py, grounding_gate.py,
the deterministic kernel, the test set, or Phase 6C artifacts was touched.

## Test results

- scripts/fte_fyjc_51_model_provider_test.py: 15/15 PASS
- tests/test_chat_assistant.py: 35/35 PASS
- scripts/fte_fyjc_46_real_ai_specialist_test.py: 60/61 PASS (1 skipped, preexisting)

No Qwen download was required to run any of these. No external AI API was
required. No model was loaded.

## Commit

COMMIT_HASH: ca00b6d

Push:
- local before push: main was up to date with origin/main (25689c4)
- push: origin/main updated to ca00b6d
- PUSH_STATUS: pushed successfully

## What was implemented

1. A clean ModelProvider boundary in backend/model_provider/.
2. A minimal Protocol-style contract in base.py (status(), interpret()),
   plus ProviderConfig, ProviderStatus, InterpretationResult, and a small
   error taxonomy.
3. A concrete LocalHFModelProvider that delegates to the existing
   LocalModelRunner. It does not duplicate tokenizer/model/adapter loading.
4. Pinned production model/adapter identity stored as constants and reflected
   in ProviderConfig. The provider reports this identity and does not reach
   out to Hugging Face to ask for “latest”.
5. Fail-closed behavior for:
   - empty input
   - model unavailable
   - generation failure
   - malformed JSON
   - forbidden accounting fields
6. Lazy loading preserved: importing the boundary does not load the model.
7. Compatibility preserved: FYJCLLMSpecialist, MockModelRunner, and the
   existing one-shot helpers still import and behave as before.
8. Security: no HF_TOKEN stored, printed, logged, or exposed in the new code.
   The provider uses environment/secrets at the point of model loading, never
   in source.

## Architecture note

This is the ModelProvider boundary only. It is not the Kernel, not the
verification wiring, not the API, and not the UI.

The boundary currently:
- accepts raw student text
- returns a structured semantic interpretation candidate
- does not persist
- does not decide accounting truth

The next architectural step is Phase 7C: a deterministic Kernel boundary that
places this provider behind extraction → candidate → grounding/verification →
validated data → persistence, with the three terminal states
(MODEL_NOT_AVAILABLE / REVIEW_REQUIRED / MALFORMED) kept distinct through to
the API.

## Remaining migration debt

- Phase 7C Kernel boundary not yet built.
- Phase 7D grounding/verification connection not yet wired through the Kernel.
- Phase 7E persistence path for validated data not yet enforced for the Qwen
  flow.
- Phase 7F API exposure of MODEL_NOT_AVAILABLE / REVIEW_REQUIRED / MALFORMED
  not yet implemented.
- Phase 7G UI integration not yet done.
- Phase 7H end-to-end integration tests not yet added.

## Next step

Implement Phase 7C: the deterministic Kernel boundary and workflow ordering.
Phase 7B must not be treated as the completed Kernel/API migration.
