# PLATRIXA — PHASE 13 REPORT
## Hosted Developer API Boundary (`/v1`)

**Date:** 2026-09-14
**Classification:** **PASS**

---

## 1. Audit findings (before any change)

- **FastAPI already exists** (Phase 7F): `api/main.py` app factory
  (`create_app()`) with lazy imports, CORS, a global 500 handler, the
  already-versioned `/api/v1` prefix, and DI override hooks
  (`set_kernel`/`set_persistence`).
- **Phase 12 public interface exists** (`platrixa.Platrixa`,
  `PlatrixaConfig`, `InputError`/`ProviderError`, `PlatrixaResult`) —
  the canonical application boundary with exactly one
  `Kernel.process` call site.
- **Gap:** the Phase 7F route (`/api/v1/kernel/process`) predates
  Phase 12 — it goes HTTP → Kernel directly. No route flowed through
  the public developer interface.
- `KernelResult.success` is a computed property (`status == VERIFIED`).
- Provider `status()` never loads the model (explicit contract in
  `local_hf.py`).

**Decision (written before implementation):** reuse the existing app;
add one additive developer router mounted at top-level **`/v1`** so the
public developer contract is versioned independently of the browser-facing
`/api/v1/*` (which is untouched). No second app, no duplicate of
`/kernel/process`.

## 2. Chosen API boundary & request flow

```
HTTP  →  request-shape validation (KernelProcessRequest — existing
         canonical contract, reused verbatim; no second input format)
      →  platrixa.Platrixa.process()   (Phase 12 public interface)
      →  Kernel.process(...)           (exactly once)
      →  PlatrixaResult projection
      →  DeveloperProcessResponse      (stable versioned JSON)
```

## 3. Files changed

**New (5):**

| File | Purpose |
|---|---|
| `api/routes/developer.py` | `/v1/process`, `/v1/health`, `/v1/ready` — transport only; zero status literals; imports only the public interface + the shared 7F mapping |
| `scripts/fte_fyjc_62_hosted_api_boundary_test.py` | 55-check evidence suite |
| `docs/HOSTED_API.md` | API documentation (12 sections) |
| `examples/developer_interface/api_curl.sh` | curl client example |
| `examples/developer_interface/api_python.py` | Python HTTP client example |

**Modified (2, minimal):**

| File | Change | Why |
|---|---|---|
| `api/main.py` | +27 lines: mount `developer.router`; body-size middleware for `/v1/*` | reuse the existing app; size cap must precede body parsing |
| `api/schemas.py` | +50 lines: `DeveloperProcessResponse`, `DeveloperHealthResponse`, `DeveloperReadyResponse` | versioned response contract |

**Intentionally untouched:** `api/routes/kernel.py` (Phase 7F route —
byte-identical), `backend/kernel/*`, `backend/model_provider/*`,
`backend/maths/*`, `backend/persistence/*`, `backend/rules/*`,
`platrixa/*`, `frontend/*`, `hf_space/`, all locked test data.

## 4. Endpoint contract

| Method | Path | HTTP mapping |
|---|---|---|
| `POST /v1/process` | process one transaction | `VERIFIED`/`REVIEW_REQUIRED`/`BLOCKED` → 200 · `VALIDATION_FAILED`/`GROUNDING_FAILED`/`FORBIDDEN_OUTPUT`/`UNSUPPORTED_TRANSACTION` → 422 · `MODEL_UNAVAILABLE` → 503 · `InputError` → 422 `INPUT_INVALID` · `PlatrixaError` → 503 `PROVIDER_UNAVAILABLE` · body > 64 KiB → 413 |
| `GET /v1/health` | liveness | constructs nothing, calls nothing — cannot trigger a model load |
| `GET /v1/ready` | readiness | provider status (never loads the model; cold-but-loadable = ready) + rule-pack summary; failures reported with a reason |

The route module contains **zero status literals** (proven by AST scan,
check E1) — the Kernel status → HTTP status mapping is imported from the
Phase 7F route, so there is exactly one mapping and one owner of the
taxonomy. The HTTP layer can never create or upgrade a state, and
`VERIFIED` can only originate from the Kernel.

## 5. Security baseline (implemented)

- Body-size cap (64 KiB) enforced by middleware **before** body parsing (413)
- JSON-only via the request schema; malformed/invalid JSON → 422
- No input repair at the API layer — invalid input is rejected (422), never transformed into a successful request
- Rule packs come only from `PLATRIXA_RULE_PACK_PATH` (trusted server config); **the request schema has no rule/hook field** — clients cannot submit executable code
- Error envelope: machine-readable code + message + request id; no stack traces, filesystem paths, or credentials
- Logging is metadata-only (endpoint, request id, duration, state, error category); financial content is never logged (proven, check O2)
- Secret scan of all new files: clean

## 6. Observability & idempotency

- Structured request logging with correlation id (safe metadata only).
- **Idempotency:** duplicate submissions are NOT guaranteed to collapse
  into one processing operation. No deduplication, no retries; each
  accepted request runs the Kernel once. Distributed exactly-once
  semantics are documented as future work (no pretense).

## 7. Configuration

Provider selection via the existing `PLATRIXA_MODEL_*` environment
family (`auto` = the Kernel's own selection point). No new env vars, no
hard-coded credentials, no authentication (explicitly out of scope per
the phase rules).

## 8. Local startup

```bash
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Boots with no provider keys, no database, no model download; the model
loads lazily on first processing request.

## 9. Tests — exact results

**New Phase 13 suite (`fte_fyjc_62`): 55/55 PASS**, covering all
required proofs A–R plus the end-to-end integration section S:
HTTP → public interface → real Kernel (stub provider) → deterministic
result → HTTP JSON, with exactly one `Kernel.process` and exactly one
provider `interpret` per request, real facade input-contract errors
surfacing as 422 `INPUT_INVALID`, RulePack pass/fail through the API,
server-side hook execution, VERIFIED-smuggling sanitized
(`decision_hint_rejected` recorded), byte-identical deterministic
bodies, and metadata-only logging.

**Full regression: 571 checks, 0 failures, 0 skipped across 14 suites:**

| Suite | Result |
|---|---|
| 7B model provider (51) | 15/15 |
| 7C kernel boundary (52) | 20/20 |
| 7D grounding wiring (53) | 13/13 |
| 7E persistence (54) | 80/80 |
| 7F FastAPI boundary (55) | 61/61 |
| 7G UI boundary (58_ui) | 46/46 |
| 7H cold-start seam (56) | 40/40 |
| 7R remote provider (57) | 43/43 |
| 7T HF-Gradio transport (58_hf) | 49/49 |
| Phase 9 status contract (59) | 20/20 |
| Phase 10 rule pack (60) | 46/46 |
| Phase 12 developer interface (61) | 65/65 |
| **Phase 13 hosted API (62)** | **55/55** |
| Legacy persistence | 18 OK |

**One genuine regression was caught and fixed during development:** the
initial top-level `from platrixa.errors import …` in the new route pulled
the backend kernel/provider graph at app import time, failing 7F's
clean-import guard (`/health` must serve with zero model/provider
modules loaded). Fixed by deferring the import into the request handler.
No old test was modified.

## 10. Deployment findings (honest)

- The runtime depends on an external model provider: ZeroGPU quota and
  cold starts can make processing slow (tens of seconds) or temporarily
  unavailable (`MODEL_UNAVAILABLE` → 503, surfaced fail-closed).
- **Not claimed production-ready / hosted-ready** in the SaaS sense: no
  authentication, rate limiting (platform-level controls only),
  billing, multi-tenancy, or SDK. The phase proves the API boundary.
- Render hosting serves the existing `/api/v1/*` app; the new `/v1/*`
  routes ride the same deployment automatically.

## 11. Git status (no commit, no push — per phase rules)

```
HEAD: c3c10b3 (Phase 12 release)
tracked modifications (2):
  M api/main.py        (+27/−1)
  M api/schemas.py     (+50)
new untracked (5):
  api/routes/developer.py
  docs/HOSTED_API.md
  examples/developer_interface/api_curl.sh
  examples/developer_interface/api_python.py
  scripts/fte_fyjc_62_hosted_api_boundary_test.py
untracked total: 51  (45 pre-existing artifacts untouched + 6 new)
```

`hf_space/` not added; all pre-existing artifacts untouched.

## 12. PASS justification

1. Phase 13 suite passes (55/55) including the end-to-end integration proof
2. All regression suites pass (571 checks, 0 failures); the one caught
   regression was fixed in the implementation, not in tests
3. Request flow is exactly HTTP → public interface → Kernel (proven
   dynamically and statically); no second execution path; HTTP layer
   holds zero status authority
4. Security baseline implemented and tested
5. Docs, examples, and local startup documented with the exact command
