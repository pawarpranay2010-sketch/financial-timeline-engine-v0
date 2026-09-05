# PLATRIXA — PHASE 7A ARCHITECTURE AUDIT

**Audit only.** No files read for modification, no code changed, no model executed, no benchmark rerun.

---

## A. CURRENT REPOSITORY MAP

Key directories present at the repo root / backend:

- `./backend/` — main Python backend package
  - `backend/database/` — SQLAlchemy ORM models, FYJC SQL schema + migration/export
  - `backend/gateway/` — AI provider gateway: `ai_executive.py`, `router.py`, `normalized_response.py`, `provider_manager.py`, `provider_adapter.py`, `admission_controller.py`, `capability_registry.py`, `redis_quota.py`, plus `backend/gateway/providers/` (Cerebras, Cohere, GitHub, Google, Groq, NVIDIA, OpenRouter, RapidAPI, SambaNova adapters)
  - `backend/intelligence/` — e.g. `calculation_safety_gate.py`
  - `backend/extraction2/` — extraction pipeline
  - `backend/module4/` — market-data provider layer with its own provider architecture (`provider/`, `provider_manager.py`, `provider_orchestrator.py`, `db_cache.py`, `redis_cache.py`, etc.)
  - `backend/maths/` — FYJC accounting core: contract, schema verifier, grounding gate, AI specialist, LLM specialist, local model runner, AI adapter interface, orchestration, BK reasoning, authority modules, problem engine, etc.
  - `backend/` root-level legacy modules too (e.g. `fyjc_student_ui.py`, `fyjc_db_persistence.py`, `chat_assistant.py`, `assignment_agent.py`, etc.)
- `./api/` — FastAPI application (`main.py`, `routes/`, `services/`, `schemas.py`)
- `./frontend/` — browser UI (`app.js`, `index.html`, `styles.css`)
- `./core/` — shared engine config/exceptions/constants/validation (`EngineSettings`, `SecretsProvider`, `FinancialTimelineEngineError` hierarchy, etc.)
- `./tests/` — existing pytest-style tests
- `./training/` — Phase 6 training artifacts (Phase 6B/6C), Phase 5 dataset in `training_data/`
- `./notebooks/`, `./scripts/`, `./docs/`
- Root-stage Streamlit app still present: `app (1) (9).py` at repo root
- Untracked artifacts in working tree (not evaluated as part of the committed architecture map): e.g. `backend/maths/fyjc_ai_adapter.py`, `content_bank/`, `core/`, various `scripts/`, `training_data/` dumps, etc. — these exist on disk but are not necessarily committed.

**Folders explicitly missing (root-level):**

- `kernel/` — MISSING
- `model_provider/` — MISSING
- `verification/` — MISSING
- `schemas/` — MISSING
- `database/` (root-level DB layer separate from backend) — MISSING

**Backend subdirectories missing (target architecture expects them):**

- `backend/kernel/` — MISSING
- `backend/model_provider/` — MISSING
- `backend/verification/` — MISSING
- `backend/schemas/` — MISSING

Note: `backend/database/` DOES exist. `backend/api/` exists as the FastAPI layer. `backend/maths/` exists and holds the FYJC authoritative logic.

---

## B. ARCHITECTURE COMPONENT STATUS TABLE

| Component | Exists? | Classification | Actual Files | Current Responsibility | Target Responsibility | Gap |
|---|---|---|---|---|---|---|
| UI | EXISTS | EXISTS BUT SCATTERED / MIXED | `frontend/app.js` (FastAPI-backed web UI), `app (1) (9).py` (root Streamlit), `backend/fyjc_student_ui.py` + related Streamlit-flavored FYJC UI modules | Two active client surfaces: browser frontend on FastAPI, plus Streamlit root app and Streamlit-flavored FYJC student UI scripting | Single UI layer (or cleanly separated Web UI + Streamlit path) feeding API | Streamlit UI is directly coupled into model/business logic; no single clean UI boundary for Phase 7 |
| API | EXISTS | EXISTS BUT NEEDS RESHAPING | `api/main.py`, `api/routes/*`, `api/services/*`, `api/schemas.py` | FastAPI serving intelligence/market/health routes; Phase 6 pipeline imported lazily in services | Kernel/orchestrator-facing API with explicit unavailable/review-required/malformed states | Current API error model is generic HTTP exceptions; FYJC specialist error taxonomy (MODEL_NOT_AVAILABLE / REVIEW_REQUIRED / MALFORMED) is not wired as first-class API contract today |
| Kernel / Orchestrator | EXISTS BUT SCATTERED | EXISTS BUT SCATTERED / EXISTS BUT NEEDS RESHAPING | `backend/maths/fyjc_orchestration.py` (deterministic authority composition), `backend/maths/fyjc_problem_engine.py`, `backend/gateway/ai_executive.py` (AI-side orchestrator), `backend/module4/provider_orchestrator.py` (market-data side), many authority modules | Several distinct “orchestrator” pieces in different domains (AI gateway, market-data module4, FYJC authority composition). No single Kernel module named “kernel” | Kernel sits between API and retrieval/data/model/calculation/verification/news/filing/report components, controls workflow, enforces boundaries | No unified Kernel abstraction today; FYJC workflow is split between specialist → grounding gate → deterministic kernel logic, but not packaged as an explicit Kernel component with the proposed top-level flow |
| Retrieval / Data | EXISTS BUT SCATTERED | EXISTS BUT SCATTERED | `backend/module4/` (market data providers), `backend/database/`, `ingestion/`, `backend/extraction2/`, `backend/intelligence/` | Data ingestion, market-data collection, extraction, retrieval-style logic spread across modules | Document/input → extraction → structured candidate output → (grounding/retrieval) → verification → validated data → persistence | Retrieval/grounding boundary exists in FYJC as grounding gate; broader RAG/data retrieval exists but is not unified into a single kernel-facing component |
| Model | EXISTS BUT SCATTERED | EXISTS BUT SCATTERED | `backend/maths/fyjc_local_model_runner.py`, `backend/maths/fyjc_llm_specialist.py`, `backend/maths/fyjc_ai_specialist.py`, `backend/maths/fyjc_ai_adapter.py`, `training/phase6c_evaluate.py`, Phase 6B/6C artifacts | Model loading, specialist interpretation, adapter interface, and evaluation all present but scattered across maths/ and training/; no single model-provider boundary for Phase 7 production path | ModelProvider abstraction with pinned revisions, non-persistent output contract, structured semantic output only | No dedicated model_provider module; model logic is reachable today through maths/ specialist modules and training code, not through a clean provider interface for the kernel |
| Calculation | EXISTS | EXISTS BUT NEEDS RESHAPING (relative to kernel boundary) | `backend/maths/fyjc_bk_reasoning.py`, `backend/maths/fyjc_accounting.py`, `backend/maths/fyjc_orchestration.py`, many authority modules, `backend/maths/*` | Deterministic accounting rules are implemented and largely untouched in FYJC domain | Deterministic calculation authority after verification | Calculation exists and is strong; the gap is architectural packaging under a kernel boundary, not the absence of logic |
| Verification | EXISTS BUT SCATTERED | EXISTS BUT SCATTERED | `backend/maths/schema_verifier.py`, `backend/maths/fyjc_grounding_gate.py`, `backend/maths/fyjc_contract.py`, `backend/maths/fyjc_ai_adapter.py` (grounding/confidence gate), `backend/intelligence/calculation_safety_gate.py` | Schema validation + grounding gate + safety gates exist and are good; spread across maths/ and intelligence/ | Verification/grounding boundary before persistence; distinct error taxonomy preserved | No top-level `verification/` module; FYJC verification logic is solid but not packaged as a first-class kernel boundary component |
| News / Filing / Report | EXISTS (partially) | EXISTS BUT SCATTERED | `backend/database/models.py` includes `News`, `Filing`, etc.; `backend/extraction2/`, `backend/memo_presenter.py`, `backend/ocr_verifier.py`, reporting-style modules | Some report/filing types exist in DB models and extraction/presentation modules | News/filing/report components sitting below kernel, feeding validated data | Not unified as kernel-facing components; present as DB models + helpers rather than as clean components in the target pipeline |
| Database | EXISTS | EXISTS | `backend/database/db.py`, `backend/database/models.py`, `backend/database/fyjc_schema.sql`, `backend/database/fyjc_migrate.py`, `backend/database/fyjc_export.py`, `backend/database/fyjc_init_db.py`, plus `backend/module4/db_cache.py` etc. | PostgreSQL persistence via SQLAlchemy; FYJC schema exists | Persistence only after validation boundary | Persistence exists; the key audit question is whether model-generated data can bypass verification — see section H |
| Configuration abstraction | EXISTS | EXISTS | `core/config.py` (EngineSettings, SecretsProvider, CompositeSecretsProvider, etc.), `core/constants.py` | Centralized engine settings + secrets abstraction, currently Streamlit-biased but backend-ready | Host exact model/adapter revisions cleanly without modifying now | EngineSettings exists; adding pinned Qwen model/adapter revisions is straightforward in principle, though today EngineSettings is oriented around gateway model selection rather than FYJC specialist artifacts |

---

## C. MISSING FOLDERS / COMPONENTS

Missing at root:

- `kernel/` — no top-level kernel package
- `model_provider/` — no dedicated model provider package
- `verification/` — no top-level verification package
- `schemas/` — no top-level schemas package (API schemas live under `api/schemas.py` instead)
- `database/` — no separate root DB package; persistence lives under `backend/database/`

Missing under `backend/`:

- `backend/kernel/`
- `backend/model_provider/`
- `backend/verification/`
- `backend/schemas/`

Present but not in the proposed target shape:

- `backend/database/` exists (good)
- `backend/api/` exists (good)
- `backend/gateway/` exists (AI provider gateway — this is the existing AI access layer, not the future FYJC ModelProvider)
- `backend/maths/` exists (FYJC authoritative logic — this is the closest thing to a “kernel” today, but it is not organized as an explicit kernel/orchestrator component for Phase 7)

---

## D. EXISTING BUT SCATTERED LOGIC

1. **Model / inference logic** — `backend/maths/fyjc_local_model_runner.py`, `backend/maths/fyjc_llm_specialist.py`, `backend/maths/fyjc_ai_specialist.py`, `backend/maths/fyjc_ai_adapter.py`. This is the closest existing code to a “QwenPlatrixaProvider” responsibility, but it is embedded in the FYJC maths specialist chain rather than exposed as a provider interface.

2. **Kernel / orchestration logic** — `backend/maths/fyjc_orchestration.py` is a real orchestrator for FYJC authority composition, but there are also other “orchestrator” modules in other domains (`backend/gateway/ai_executive.py`, `backend/module4/provider_orchestrator.py`). There is no single kernel module that owns the end-to-end path UI → API → Kernel → components → DB.

3. **Verification / grounding** — Schema validation (`schema_verifier.py`), grounding gate (`fyjc_grounding_gate.py`), contract (`fyjc_contract.py`), and AI adapter gating (`fyjc_ai_adapter.py`) all exist and are good, but they live under `backend/maths/` and `backend/intelligence/`, not under a dedicated `verification/` boundary.

4. **Configuration** — `core/config.py` already provides `EngineSettings` and `SecretsProvider`, so the configuration backbone exists; the future pinned model/adapter revisions can be added there without inventing a new config system.

5. **Provider architecture** — There is a mature provider architecture for the AI gateway (`backend/gateway/providers/*`, `provider_manager.py`, `provider_adapter.py`), and a separate provider architecture for market data (`backend/module4/provider/*`). This is useful scaffolding for ModelProvider thinking, but the FYJC specialist path currently does not sit behind that gateway.

---

## E. MODEL INTEGRATION MAP

Current model-related files:

- `backend/maths/fyjc_local_model_runner.py` — lazy-loading Hugging Face model runner; reads `PLATRIXA_FYJC_MODEL_ID`, `PLATRIXA_FYJC_ADAPTER`, device/dtype/max-tokens/temperature from env; supports PEFT LoRA adapters from a local path.
- `backend/maths/fyjc_llm_specialist.py` — FYJC local model specialist; builds system prompt, extracts JSON, runs strict validation, delegates to `LocalModelRunner`.
- `backend/maths/fyjc_ai_specialist.py` — deterministic rule-based FYJC AI specialist (Phase 2), no model.
- `backend/maths/fyjc_ai_adapter.py` — abstract `AIAdapter` interface + `AIInterpretation` contract for a future specialized finance model.
- `training/phase6c_evaluate.py` — Phase 6C evaluator; loads Qwen base and LoRA adapter for benchmark comparison.
- Phase 6 manifests and training code under `training/`.

Key facts for Phase 7 model integration:

- The model path currently used by FYJC specialist modules is Hugging Face Transformers + optional local PEFT adapter. It is not currently integrated through the `backend/gateway` provider system.
- The future `QwenPlatrixaProvider` should likely **wrap existing model runner/specialist logic** rather than be built entirely from scratch, since `LocalModelRunner` already exists and already supports LoRA. The Phase 6C frozen artifacts (pinned revisions, prompts, decoding, leakage/grounding criteria) must remain untouched.
- Model integration today is **scattered** and **not behind a single ModelProvider boundary**.

---

## F. KERNEL / ORCHESTRATION MAP

Today there is no package literally named “kernel”. The closest things are:

- `backend/maths/fyjc_orchestration.py` — deterministic orchestration/authority composition for FYJC transactions.
- `backend/gateway/ai_executive.py` — orchestrator for AI provider calls (gateway domain).
- `backend/module4/provider_orchestrator.py` — orchestrator for market-data providers (module4 domain).
- Various maths/ modules that implement authoritative FYJC logic.

So:

- **Is there a Kernel under another name?** Partially, yes — FYJC has a strong deterministic kernel/orchestration story inside `backend/maths/`, but it is not packaged as the top-level kernel component in the proposed architecture diagram. The proposed flow `UI → API → Kernel → components → DB` currently has no single Kernel module owning it.

---

## G. GROUNDING + VERIFICATION BOUNDARY

FYJC grounding/verification boundary today:

1. Model/specialist produces candidate structured interpretation.
2. `schema_verifier.py` validates the structured interpretation contract (legacy 7-field and expanded 18-field).
3. `fyjc_grounding_gate.py` (`ExpandedGroundingGate`) checks grounding against source text, forbids AI-claimed VERIFIED, rejects forbidden accounting fields, etc.
4. Deterministic kernel/authority logic proceeds only after the gate.
5. Persistence is done by separate DB modules.

Important: the grounding/verification logic is strong in FYJC. The gap for Phase 7 is **architectural packaging and API exposure**, not the absence of verification logic.

---

## H. DATABASE / PERSISTENCE MAP

Persistence today:

- `backend/database/models.py` — SQLAlchemy ORM models (Company, Financial, MarketPrice, News, Filing, CorporateAction, etc.).
- `backend/database/db.py` — SQLAlchemy `Base`/engine setup.
- `backend/database/fyjc_schema.sql`, `backend/database/fyjc_migrate.py`, `backend/database/fyjc_export.py`, `backend/database/fyjc_init_db.py` — FYJC DB artifacts.
- `backend/module4/db_cache.py`, `backend/module4/database_manager.py`, `backend/module4/redis_cache.py` — caching/persistence helpers in the market-data domain.
- Root/legacy persistence helpers also exist in `backend/` (e.g. `fyjc_db_persistence.py`).

Persistence audit for the non-negotiable data boundary:

- The FYJC specialist/model path already has a clear conceptual separation: model produces interpretation candidate → grounding/verification → deterministic kernel → persistence.
- **Risk to watch:** today there are many DB-writing modules across `backend/`. The Phase 7 integration must ensure the Qwen model path cannot write directly to PostgreSQL/vector/any persistent store. The model should produce candidate structured output only, and persistence should happen only after the appropriate validation boundary. This is an architectural wiring constraint, not a claim that the current FYJC math modules violate it — but given the size and age of `backend/`, there is enough surface area that this must be explicitly enforced during Phase 7, not assumed.

---

## I. API MAP

Current API:

- `api/main.py` — FastAPI app factory, serves frontend static files at `/`, mounts `/api/v1` routers for health/market/intelligence.
- `api/routes/` — route modules.
- `api/services/` — service layer (Phase 6 pipeline imported lazily here).
- `api/schemas.py` — request/response schemas.
- `backend/gateway/router.py` — provider routing logic with task-type constants.
- `backend/gateway/normalized_response.py` — `NormalizedResponse` dataclass.

Error-state audit:

- The existing gateway/Stack has rich provider errors (`ProviderError` hierarchy in `core/exceptions.py`, `AllProvidersFailedError`, circuit breaker, etc.).
- However, the Phase 7 target error taxonomy — `MODEL_NOT_AVAILABLE`, `REVIEW_REQUIRED`, `MALFORMED` — is **not currently exposed as a distinct first-class API contract** for the FYJC specialist path. FYJC specialist code does use `MODEL_NOT_AVAILABLE` internally (`backend/maths/fyjc_llm_specialist.py`), but the API layer is not organized around surfacing those three states distinctly end-to-end.

---

## J. UI MAP

Current UI surface:

- `frontend/app.js` + `frontend/index.html` + `frontend/styles.css` — browser-based institutional terminal served by FastAPI.
- `app (1) (9).py` — Streamlit app at repo root (still present).
- Streamlit-flavored FYJC student UI logic scattered in `backend/` (e.g. `fyjc_student_ui.py`, `fyjc_student_session.py`, `fyjc_practice_ui.py`, and many maths/ modules importing Streamlit).

Active/current vs legacy/scaffolding:

- The FastAPI + browser frontend path appears to be the current web backend/UI path.
- The Streamlit root app and Streamlit-flavored FYJC UI modules are clearly still present and coupled into business/model logic. They are not just legacy scaffolding; they are live code paths that import Streamlit and use `st.session_state`/`st.secrets` directly.

Streamlit coupling is widespread: many modules import Streamlit or reference `st.` directly. That coupling is exactly the kind of thing a ModelProvider/Kernel integration must be careful not to deepen.

---

## K. TEST MAP

Existing tests observed:

- `tests/test_e2e_pipeline.py`, `tests/test_e2e_pipeline_v2.py`, `tests/test_chat_assistant.py`, `tests/test_provider_secret_config.py`, `tests/test_current_ratio.py`, `tests/test_cagr.py`, `tests/test_apple_fixture_current_ratio.py`, `tests/test_calculation_safety_gate.py`, `tests/test_financials_persistence.py`, `tests/test_real_world_validation.py`, `tests/test_fx_metadata_validation.py`, `tests/test_ifrs_xbrl.py`, `tests/test_period_association.py`, `tests/test_scale_propagation.py`, `tests/test_yfinance_integration.py`, plus `tests/test_data/`.
- Many scripts under `scripts/` that function as targeted regression/audit tests (e.g. `fte_fyjc_*.py`).
- Phase 6C preparation test: `scripts/fte_fyjc_50_phase6c_preparation_test.py` — NOT TO BE TOUCHED per Phase 6C freeze.

Tests that must remain green during Phase 7 (at minimum):

- Anything covering the FYJC deterministic kernel/contract/grounding/schema verifier.
- Anything covering the existing provider/gateway contract if Phase 7 touches API error paths.
- Anything covering the existing FastAPI route/service contracts if Phase 7 changes the API layer.
- Phase 6C preparation test and Phase 6C artifacts.

I did not run the test suite in this audit (audit only).

---

## L. ARCHITECTURAL RISKS

1. **No dedicated ModelProvider boundary today.** Model/specialist logic is reachable through `backend/maths/` modules and training code rather than a single provider interface; introducing one later could be confused with the existing gateway provider architecture unless the boundary is clearly drawn.

2. **No single Kernel module.** “Kernel” work is split across FYJC maths orchestration, gateway AI executive, and module4 orchestrator. Without an explicit kernel boundary, it is easy for Phase 7 integration to implicitly let model output skip verification or let persistence sneak in too early.

3. **Model output could bypass verification if wired naively.** Persistence is present and widespread; the non-negotiable rule — model never writes directly to PostgreSQL/vector/any persistent store — must be enforced by architecture, not by hope.

4. **Error taxonomy not wired end-to-end.** `MODEL_NOT_AVAILABLE`, `REVIEW_REQUIRED`, `MALFORMED` are distinguishable in code today, but not as a clean API-visible contract for the specialist path. Risk of collapsing them into generic errors during integration.

5. **Floating model revisions risk.** Model runner config currently leans on environment variables and default constants; without pinning in the production path, “latest” could be used accidentally. The Phase 7 ModelProvider must pin exact revisions and fail closed on mismatch.

6. **Streamlit coupling is broad.** Many modules import Streamlit or use `st.` directly; adding a model provider/kernel path must not make this coupling worse, and should ideally reduce it for the production path.

7. **Two live UI paths.** FastAPI+browser frontend and Streamlit root app coexist; Phase 7 must decide which UI path the Qwen-integrated flow belongs to, or explicitly support both without duplicating business logic.

8. **Scattered provider architectures.** Gateway providers and module4 providers are both real; a new FYJC ModelProvider should not accidentally duplicate or collide with them.

9. **Phase 6C freeze discipline.** Phase 6C artifacts are strong but must be treated as frozen evidence; the biggest risk is accidental drift during integration (prompts, revisions, decoding, leakage/grounding criteria).

10. **Uncommitted/working-tree artifacts.** Several Phase 6/Platrixa AI artifacts exist untracked on disk (`backend/maths/fyjc_ai_adapter.py`, `content_bank/`, various `training_data/` dumps, etc.). Their relationship to committed architecture should be clarified before Phase 7 changes are made, to avoid integrating against something that is not actually part of the committed baseline.

---

## M. PROPOSED SMALL-COMMIT PHASE 7 PLAN

### Phase 7A — audit (this phase)
- **Files likely to change:** none (documentation-only audit output may be added if desired).
- **Files that must NOT change:** Phase 6C artifacts, FYJC maths kernel/contract/grounding/schema verifier, test set, model/adapter revisions, prompts, metrics.
- **Contract introduced:** none yet.
- **Tests required:** none beyond confirming no accidental modifications.
- **Rollback boundary:** trivial — no changes.
- **Acceptance criteria:** this audit report exists and the integrated team agrees on the component map and risks.

### Phase 7B — ModelProvider boundary
- **Goal:** introduce a `backend/model_provider/` boundary for the FYJC Qwen path, wrapping existing runner/specialist logic, pinning exact revisions, and enforcing “model produces candidate structured output only; never writes to persistent store.”
- **Files likely to change:** new model_provider modules; possibly extend `core/config.py` or a new model config to pin base model/adapter revisions; minimal changes to existing maths/ modules only to route through the boundary.
- **Files that must NOT change:** Phase 6C evaluator, prompts, decoding, leakage/grounding criteria, test set, Phase 6 manifests.
- **Contract introduced:** ModelProvider interface with revision pinning, availability status, structured candidate output, and explicit `MODEL_NOT_AVAILABLE` path; no persistence method on the provider.
- **Tests required:** unit tests for the provider boundary, revision pinning enforcement, availability/error status, structured-output-only contract; mock inference so the real model is not required.
- **Rollback boundary:** new modules can be removed/added without touching FYJC kernel logic if the boundary is cleanly layered.
- **Acceptance criteria:** model path is accessed only through ModelProvider; revision mismatch fails closed; provider has no direct DB/vector write path.

### Phase 7C — Kernel integration
- **Goal:** introduce/clean up an explicit Kernel component that owns the workflow path and enforces ordering: input → extraction → candidate output → grounding/retrieval → verification → validated data → persistence.
- **Files likely to change:** new/reshaped kernel module(s); likely some re-packaging of existing orchestration/verification/calculation calls.
- **Files that must NOT change:** deterministic accounting logic, grounding gate implementation, Phase 1 contract.
- **Contract introduced:** Kernel orchestrates components and enforces the verification-before-persistence boundary; returns distinct terminal states including `REVIEW_REQUIRED` and `MALFORMED` where appropriate.
- **Tests required:** kernel orchestration unit tests with mocked components; workflow ordering tests; state-distinction tests.
- **Rollback boundary:** kernel can be kept as a thin orchestrator over existing modules; if it breaks, the underlying modules should still be individually usable.
- **Acceptance criteria:** kernel expressible as a small, inspectable layer; no component can silently persist model output before verification.

### Phase 7D — grounding/verification connection
- **Goal:** make the Kernel’s verification/grounding boundary explicit and API-visible for the Qwen path, reusing existing schema verifier + grounding gate.
- **Files likely to change:** wiring code, possibly a small verification boundary module; existing verifier/gate logic should be reused, not rewritten.
- **Files that must NOT change:** `fyjc_grounding_gate.py`, `schema_verifier.py`, `fyjc_contract.py`.
- **Contract introduced:** explicit compatibility between ModelProvider output and verification boundary; clear pass/Review-required/malformed outcomes.
- **Tests required:** verification boundary tests using existing verifier/gate; synthetic candidate outputs covering valid, malformed, and forbidden-field cases.
- **Rollback boundary:** verification logic already exists; this phase is mostly about connecting it into the kernel/API path.
- **Acceptance criteria:** model candidate output cannot become “trusted data” without passing the existing verification/grounding boundary.

### Phase 7E — database persistence
- **Goal:** ensure persistence for the Qwen path happens only after verification, and that the model path has no direct write access.
- **Files likely to change:** persistence wiring for validated data; possibly a small repository/DAO boundary if one does not already exist in the intended shape.
- **Files that must NOT change:** ORM models unnecessarily; existing FYJC DB schema unless a real need is found.
- **Contract introduced:** persistence is a downstream step in the kernel workflow, reachable only from validated output.
- **Tests required:** persistence tests ensuring model-only output cannot be persisted directly; integration tests for the validated-data path.
- **Rollback boundary:** persistence layer changes should be separable from kernel/model changes.
- **Acceptance criteria:** no code path allows model-generated candidate output to reach PostgreSQL/vector/any persistent store without passing verification.

### Phase 7F — API
- **Goal:** expose the Qwen-integrated flow through the API with distinct `MODEL_NOT_AVAILABLE` / `REVIEW_REQUIRED` / `MALFORMED` states where appropriate.
- **Files likely to change:** API routes/services/schemas for the new flow; error contract for those states.
- **Files that must NOT change:** unrelated existing API routes unless explicitly part of the integration.
- **Contract introduced:** API responses/schema distinguish availability failure, review-required, and malformed; not collapsed into generic errors.
- **Tests required:** API contract tests for the three states; error-path tests.
- **Rollback boundary:** new route/service can be added/removed without disturbing unrelated routes.
- **Acceptance criteria:** client can tell the three states apart from API responses.

### Phase 7G — UI
- **Goal:** connect the chosen UI path (FastAPI+browser frontend and/or Streamlit) to the new API flow with proper error-state rendering.
- **Files likely to change:** UI components/clients for the new flow; possibly Streamlit decoupling work if the production path is chosen there.
- **Files that must NOT change:** existing UI unrelated to the new flow unless part of integration.
- **Contract introduced:** UI renders unavailable/review-required/malformed distinctly.
- **Tests required:** UI-level integration tests or at least API-driven rendering tests for error states.
- **Rollback boundary:** UI changes should be separable from backend changes.
- **Acceptance criteria:** UI does not treat review-required as a crash, and does not treat malformed as model-unavailable.

### Phase 7H — end-to-end integration tests
- **Goal:** add integration tests covering the full path through the Kernel with mocked model/components where needed.
- **Files likely to change:** new integration tests; possibly test fixtures.
- **Files that must NOT change:** Phase 6C artifacts; test set.
- **Contract introduced:** end-to-end behavior is covered without running the real 100-example benchmark or the real model in the repository CI path unless intentionally chosen.
- **Tests required:** e2e tests with mocked model/verification/persistence; tests that confirm the non-negotiable boundaries hold.
- **Rollback boundary:** integration tests are additive.
- **Acceptance criteria:** critical Phase 7 boundaries are regression-covered.

---

## N. “DO NOT TOUCH” LIST

For this audit and for Phase 7 integration in general:

- `training/phase6c_evaluate.py` and all Phase 6C artifacts
- `training/phase6b_manifest.json`, `training/phase6_manifest.json`, `training/PHASE6_README.md`
- `training_data/fyjc_specialist_test.jsonl` and the locked 100-example test set
- Model base revision `Qwen/Qwen2.5-1.5B-Instruct` at `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`
- LoRA adapter `Pranay-20/platrixa-fyjc-specialist-v0.1` at `b5c0a37cebc00e93144150dbbcaa7b28cadb259e`
- Evaluation prompts, decoding settings, hallucination/leakage criteria, benchmark methodology
- `backend/maths/fyjc_contract.py`, `backend/maths/schema_verifier.py`, `backend/maths/fyjc_grounding_gate.py` (grounding/verification boundary logic)
- Deterministic accounting kernel/authority logic in `backend/maths/` (e.g. `fyjc_bk_reasoning.py` and related authority modules)
- Phase 6C preparation test `scripts/fte_fyjc_50_phase6c_preparation_test.py`
- Any file that would change the benchmark results or the model/adapter artifacts

---

## FINAL SUMMARY

**What exists today:** a substantial, largely FYJC-specific deterministic kernel and verification story inside `backend/maths/`, a real AI provider gateway in `backend/gateway/`, a FastAPI API in `api/`, a browser frontend in `frontend/`, a still-present Streamlit root app with broad Streamlit coupling, a mature config/secrets abstraction in `core/`, and SQLAlchemy/PostgreSQL persistence in `backend/database/`.

**What is missing for the target architecture:** no top-level `kernel/`, no `model_provider/`, no `verification/`, no `schemas/`; and no single Kernel module owning the proposed `UI → API → Kernel → components → DB` flow. The Phase 7 Qwen integration is therefore less about inventing logic from scratch and more about drawing clean boundaries around what already exists — especially a ModelProvider that pins revisions and never writes to persistent store, a Kernel that enforces verification-before-persistence, and an API/UI contract that keeps `MODEL_NOT_AVAILABLE`, `REVIEW_REQUIRED`, and `MALFORMED` distinct.

**The single most important non-negotiable to enforce during Phase 7:** the Qwen model path must remain a candidate-output-only path, with persistence reachable only after verification. Everything else is packaging, wiring, and test coverage.
