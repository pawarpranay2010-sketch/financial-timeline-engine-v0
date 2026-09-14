# PLATRIXA — PHASE 11A: DEVELOPER INTERFACE AUDIT & DESIGN REPORT

**Phase type:** Audit + design only. **No implementation performed. No files moved. No commit. No push.**
Audit basis: repository at HEAD `9729069` (Phase 10 closed), read-only inspection.

---

## 1. Current repository architecture

### 1.1 Top-level structure (tracked)

| Path | Role | Production relevance |
|---|---|---|
| `api/` | FastAPI HTTP boundary (`api.main:app`) | **Production** (Render `startCommand: uvicorn api.main:app`) |
| `backend/kernel/` | `kernel.py` (Kernel, KernelResult, terminal statuses), `result.py` (declared public import surface) | **Production core** |
| `backend/model_provider/` | `base.py` (Protocol, `ProviderConfig`, `ProviderStatus`, `InterpretationResult`), `local_hf.py`, `remote_hf.py` (factory + Modal endpoint), `hf_gradio.py` (HF Space transport) | **Production core** |
| `backend/rules/` | Phase 10 rule boundary: `contract.py`, `loader.py` (YAML), `engine.py` | **Production-optional** (default off) |
| `backend/persistence/` | `base.py` contract, `postgres.py` implementation | **Production** (invoked by API route, not Kernel) |
| `backend/maths/` | 71 modules: schema verifier, grounding gate, `fyjc_accounting` (deterministic truth), specialists, local model runner | **Production core (lazy imports)** + legacy/training surface |
| `frontend/` + `frontend/functions/api/[[path]].js` | Static UI + Cloudflare 1:1 proxy to `API_BACKEND_URL` | **Production** |
| `core/` | Legacy config/logging/validation for the extraction-era stack | Not on FYJC kernel path |
| `backend/gateway/, module4/, intelligence/, database/, extraction2/` | Market-data / extraction-era subsystems | Unrelated to FYJC transaction path |
| `app (1) (9).py`, `backend/fyjc_student_ui.py`, `backend/fyjc_student_session.py` | Streamlit-era UI bundle | Test-active only; zero deployment references |
| `scripts/` | 120 phase/regression test suites (`fte_fyjc_51…60`) + legacy scripts | Test infrastructure |
| `tests/` | pytest-style tests for extraction-era stack | Not FYJC-kernel path |
| `examples/rules/` | `platrixa_rules.yaml`, `custom_hooks.py` (Phase 10 examples) | Developer-facing seed |
| `docs/` | FYJC domain docs | Documentation |
| `hf_space/` | HF Space runtime (untracked by policy) | Remote inference runtime |

**Packaging fact:** there is **no `pyproject.toml` / `setup.py` / `setup.cfg` / top-level package `__init__.py`**. The repository is not installable; every consumer today uses `sys.path` insertion (as all phase suites do) or the HTTP API. There is **no `__version__`** anywhere in `backend/`, `api/`, or `core/`.

### 1.2 The authoritative production path (verified in Phases 7/8/9/10)

```
frontend/app.js → Cloudflare Function (verbatim proxy) → FastAPI api/routes/kernel.py
→ Kernel.process(raw_input)                     [kernel.py:266+, single orchestration owner]
   → provider selection: model_provider()       [kernel.py:229–247; single decision point]
       PLATRIXA_MODEL_ENDPOINT_URL set → RemoteHFModelProvider (Modal) / HFGradioModelProvider
       unset                           → LocalHFModelProvider (cold-start seam, Phase 7H)
   → maths/schema_verifier (lazy)
   → maths/fyjc_grounding_gate (lazy, fail-closed; VERIFIED authority rule)
   → maths/fyjc_accounting.hardened_bookkeeping_outcome (lazy, deterministic truth)
   → Phase 10 rule engine (optional, downgrade-only, terminal edge)
→ KernelResult (status, status_label, interpretation_candidate, accounting_result,
   grounding_issues, issues, rule_evidence, metadata, to_dict())
→ persistence boundary (route-owned) → PostgreSQL
→ KernelProcessResponse (status map 200 / 422 / 503 / 502)
```

---

## 2. Existing developer-facing surfaces

1. **`backend/kernel/result.py`** — its docstring explicitly declares itself the *“Public import surface for KernelResult and the Kernel terminal states… so later API/Kernel consumers can import from one obvious place without reaching into implementation details.”* This is the strongest existing hook for a programmatic API and was designed for exactly this purpose.
2. **`backend/kernel/kernel.py::Kernel`** — the only sanctioned processing entry point. Constructor is already configuration-shaped: `Kernel(model_provider=…, provider_config=…, rule_pack=…, rule_hooks=…)`. `Kernel.process(raw_input: str) -> KernelResult` is the single method.
3. **`backend/model_provider/base.py`** — documented application-facing contract (`ModelProvider` Protocol, frozen `ProviderConfig` with pinned base/adapter revisions, tri-state `ProviderStatus`, `InterpretationResult`). Importing it loads no model.
4. **`backend/rules/`** — `RuleHook` ABC + `RuleDecision` (frozen; VERIFIED not expressible) + `load_yaml_rule_pack()`; fail-closed at construction.
5. **`examples/rules/`** — working YAML pack + `HolidayBudgetRule` hook, proven in Phase 10 Scenario A/B.
6. **HTTP API** — `POST /api/v1/kernel/process` with `{"raw_input": "..."}` (max 2000 chars) — the product surface, not the developer surface.
7. **`PLATRIXA_*` env family** — the existing configuration mechanism (see §7).

---

## 3. Current public/private boundary problems

| # | Problem | Evidence |
|---|---|---|
| P1 | **No installable package.** A developer must clone the repo and hand-manage `sys.path`; `import backend.kernel.kernel` only works from repo root. | No `pyproject.toml`; suites self-insert repo root into `sys.path` (e.g. `fte_fyjc_60:28–29`) |
| P2 | **Import path leaks internals.** The natural-looking import is `from backend.kernel.kernel import Kernel` — an implementation module — while the intended public surface (`backend/kernel/result.py`) is under-publicized and does not export a convenience `process()`. | `result.py` header |
| P3 | **No CLI.** 120 scripts exist but all are phase/audit suites; there is no `python -m <pkg>` entry point for one-off developer processing. | `scripts/` inventory; no `__main__.py` anywhere |
| P4 | **Configuration is split across env vars and a dataclass with no single documented facade.** `ProviderConfig` exists but nothing shows a developer how local/remote selection, rule packs, and hooks combine. | §7 env inventory |
| P5 | **Legacy/competing entry points are importable.** Streamlit-era `fyjc_orchestration`, `fyjc_student_ui`, the old specialist path, and unrelated `gateway`/`module4` subsystems sit one import away and could tempt a developer into a non-authoritative path. | Phase 7I audit classifications |
| P6 | **No version exposure** for the runtime/contract a developer codes against. | No `__version__` |
| P7 | **`KernelResult.persisted` is route-level state** — persistence happens in the HTTP layer, not Kernel. A programmatic result must represent this honestly (evidence field), not pretend Kernel persists. | `api/routes/kernel.py:190–215` |

None of these are correctness defects; they are surface problems. The runtime boundaries (Kernel authority, grounding, downgrade-only rules, fail-closed construction) are intact and tested (Phases 7B–10, 449 checks green at Phase 10 closure).

---

## 4. Recommended public package boundary (11F)

**Recommendation: a new thin facade package `platrixa/` at the repository root** — the only structure the audit supports without reorganizing anything, because:

- `backend.*` is already the importable runtime; a sibling `platrixa/` package can re-export it without moving a single file.
- The name `platrixa` is already the product's identity (model adapter repo `Pranay-20/platrixa-fyjc-specialist-v0.1`, env prefix `PLATRIXA_*`), so the import name matches every existing artifact.
- A later `pyproject.toml` can make it pip-installable from the repo without touching `backend/`.

### Structure (proposed — NOT implemented)

```
platrixa/
  __init__.py        # public exports: process, PlatrixaConfig, PlatrixaResult,
                     #   statuses (re-exported terminal states), __version__
  config.py          # PlatrixaConfig dataclass (see §7) — composes existing pieces only
  _facade.py         # build_kernel(config) → Kernel; process() orchestration (thin!)
  __main__.py        # CLI (argparse stdlib; see §6)
```

Rules for the facade:
- **Re-export, never re-implement.** `PlatrixaResult` = `KernelResult` re-exported (or a documented read-only view of it); statuses re-exported from `backend.kernel.result`.
- The facade may **construct and configure** a Kernel and **shape input**; it must contain **zero accounting, grounding, validation, or rule logic**.
- `platrixa/__init__.py` must not import torch/transformers (the Kernel's own lazy-import discipline must be preserved; `import platrixa` stays cheap).
- Optional extras (rule packs) resolve paths from the caller's CWD or explicit paths — no implicit repo-root dependence beyond what exists.

---

## 5. Recommended Python API shape (11B-A, 11D)

```python
import platrixa

# Minimal (auto provider selection — identical semantics to Kernel() today):
result = platrixa.process("Purchased furniture for cash ₹15,000")

# Explicit configuration:
cfg = platrixa.PlatrixaConfig(
    provider="auto",            # "auto" | "local" | "remote"  (maps to existing selection logic)
    rule_pack="examples/rules/platrixa_rules.yaml",
    rule_hooks=[MyHook()],      # objects satisfying backend.rules.contract.RuleHook
)
result = platrixa.process("Sold goods on credit to Amit ₹18,000", config=cfg)

result.status          # VERIFIED / REVIEW_REQUIRED / BLOCKED / MODEL_UNAVAILABLE / …
result.status_label    # "Verified" / …
result.success         # existing KernelResult.success
result.interpretation_candidate   # 18-field dict
result.accounting_result          # deterministic kernel output (unchanged)
result.issues / result.grounding_issues / result.rule_evidence
result.request_id / result.metadata
result.to_dict()                  # existing KernelResult.to_dict()
```

### Contract decisions (with justification)

1. **Accepted input:** `str` only — `raw_input` is the Kernel's sole input (`KernelProcessRequest.raw_input`, 1–2000 chars). The facade applies the same length check and otherwise passes the string **verbatim**. No dict/structured input in Phase 11.
2. **Result object:** **reuse `KernelResult`** — do not invent a parallel schema. If a wrapper view is ever needed, it must be a strict projection of `to_dict()` (same rule the HTTP layer's `_safe_accounting` already applies to leaky keys). Public docs should present `to_dict()` as the stable serialization.
3. **Error behavior:** **no new exception taxonomy.** The existing fail-closed statuses are the error surface: `MODEL_UNAVAILABLE`, `VALIDATION_FAILED`, `GROUNDING_FAILED`, `FORBIDDEN_OUTPUT`, `UNSUPPORTED_TRANSACTION`, `BLOCKED`. The facade never catches-and-converts a failure into success. Only construction-time failures (e.g. malformed rule pack → the Kernel already refuses construction) may raise, matching current fail-closed semantics; the facade must let that error propagate untouched.
4. **Evidence representation:** `result.rule_evidence` (Phase 10) already answers "why was this rejected/downgraded?" — each record carries `rule_id`, `source`, `result` (PASS/FAIL/UNAVAILABLE/ERROR), `message`, `metadata`. No change.
5. **Persistence honesty:** a programmatic run does **not** persist. The facade must not call PostgreSQL; docs state that persistence is a deployment-level concern (HTTP route). Optionally expose `PlatrixaConfig(persist=False)` as the only allowed value in Phase 11 — i.e., simply document it, don't implement a persistence option.
6. **VERIFIED authority:** the facade has no status parameter and no way to inject one; VERIFIED is produced only by the deterministic accounting flow inside Kernel.process, exactly as today.

---

## 6. Recommended CLI shape (11B-B)

```
python -m platrixa process "Purchased furniture for cash ₹15,000"
python -m platrixa process "..." --provider local
python -m platrixa process "..." --provider remote
python -m platrixa process "..." --rules examples/rules/platrixa_rules.yaml
python -m platrixa process "..." --hook examples.rules.custom_hooks:HolidayBudgetRule
python -m platrixa process "..." --json
```

Design notes:
- One command (`process`) only. No server command (uvicorn is the deployment's job), no training, no eval commands in Phase 11.
- `--provider` maps onto the **existing** selection logic; `--rules`/`--hook` map onto existing Kernel parameters; hook strings resolve `module:ClassName` via `importlib` (no `eval`).
- `--json` prints `result.to_dict()` verbatim.
- Exit codes: `0` = VERIFIED, `1` = REVIEW_REQUIRED, `2` = BLOCKED, `3` = any fail-closed failure status. Deterministic, documented, purely a view of the status.

---

## 7. Recommended configuration model (11B-C)

`PlatrixaConfig` must compose **only** mechanisms that already exist:

| Config field | Backed by (existing) |
|---|---|
| `provider: "auto"\|"local"\|"remote"` | `Kernel.model_provider()` single selection point; `"auto"` = today's exact env-driven behavior (`PLATRIXA_MODEL_ENDPOINT_URL` set → remote) |
| `endpoint_url`, `endpoint_token`, `timeout`, `transport` | existing envs `PLATRIXA_MODEL_ENDPOINT_URL` / `_TOKEN` / `PLATRIXA_MODEL_TIMEOUT` / `PLATRIXA_MODEL_TRANSPORT` (config values, when set, are applied as process env or passed into `ProviderConfig`; no new env names) |
| `base_model_id`, `adapter`, `revisions`, `max_tokens`, `temperature`, `top_p` | existing `ProviderConfig` fields + `PLATRIXA_FYJC_*` overrides — defaults stay pinned to `989aa798…` / `b5c0a37…` |
| `rule_pack: Optional[str]` | `Kernel(rule_pack=…)` |
| `rule_hooks: Sequence[RuleHook]` | `Kernel(rule_hooks=…)` |
| — | **No new** env vars, no auth/billing options, no hosted flags |

`PlatrixaConfig` is a frozen dataclass; `build_kernel(cfg)` either uses the factory path (`auto`) or constructs `LocalHFModelProvider`/`RemoteHFModelProvider` explicitly for `local`/`remote` — the same classes, no new provider code.

### Local vs remote usage model (11E)

| Mode | Developer experience | What executes |
|---|---|---|
| **Local** | `PlatrixaConfig(provider="local")` + `PLATRIXA_FYJC_ADAPTER` (or HF resolution via `HF_TOKEN`) | `LocalHFModelProvider → maths/fyjc_local_model_runner` (cold-start seam; loads Qwen+LoRA on local CUDA/CPU) |
| **Remote** | `PlatrixaConfig(provider="remote")` + `PLATRIXA_MODEL_ENDPOINT_URL` (+ optional `_TOKEN`, `PLATRIXA_MODEL_TRANSPORT=gradio` for the HF Space) | `RemoteHFModelProvider` → Modal endpoint or `HFGradioModelProvider` → HF Space `/interpret_core` (`@spaces.GPU(duration=45)`) |
| **RulePack** | add `rule_pack="path.yaml"` | identical flow + downgrade-only rule evaluation at terminal edge |
| **RulePack + hooks** | add `rule_hooks=[...]` | same, plus programmatic hooks; exceptions/UNAVAILABLE fail closed (Phase 10 proven) |

No API keys beyond those mechanisms already exist; none are added.

---

## 8. Test plan (11G — designed, not implemented)

New suite `scripts/fte_fyjc_61_developer_interface_test.py`, reusing the established stub patterns from suites 51/52/57/59/60:

1. `platrixa.process` reaches `Kernel.process` **exactly once** per call (counting stub provider/kernel).
2. **Cannot bypass Kernel:** assert `platrixa/__init__.py` and `_facade.py` never import `backend.maths.*` accounting/grounding modules (static scan, same technique as the 7C/7F guards) and expose no function that does.
3. **Accounting behavior preserved:** stubbed-provider run produces the same `accounting_result` as a direct `Kernel()` run with identical inputs.
4. **VERIFIED authority:** no public symbol accepts/forwards a status; a run through the facade with the standard candidate yields the kernel-decided status; combined with the Phase 9 suite (59) this stays enforced.
5. **REVIEW_REQUIRED/BLOCKED preserved:** ambiguous input → REVIEW_REQUIRED via facade; rule-pack BLOCKED hint downgrades per Phase 10 semantics (suite 60 stays green).
6. **Downgrade-only rules:** malformed pack → facade construction fails closed (no lazy surprise); hook FAIL → downgraded status + evidence present.
7. **Fail-closed:** provider `available=False, loadable=False` → `MODEL_UNAVAILABLE` through the facade (mirrors 7H/7R stubs).
8. **Local config:** `build_kernel(provider="local")` yields a Kernel whose provider is `LocalHFModelProvider` **without loading the model** (status() only — no torch import).
9. **Remote config:** same for remote (no network call; config-only assertion).
10. **No second accounting implementation:** byte-level guard — the facade package contains no reference to `hardened_bookkeeping_outcome`, debit/credit construction, or journal building (static scan).
11. Regression coupling: suites 51–60 + legacy persistence must remain green unchanged (the facade is additive).

---

## 9. Migration risk

| Risk | Level | Mitigation |
|---|---|---|
| Name collision on PyPI / confusion with adapter repo | Low | Repo-local package; if ever published, `platrixa` runtime name is consistent with adapter naming already in use |
| Facade drifts into a "second runtime" | Medium (over time) | Static guards in suite 61 (§8 items 2, 10) + the re-export-only rule; review checklist |
| Packaging (`pyproject.toml`) accidentally ships `backend/` internals or Streamlit-era code | Medium | Phase 11+ packaging must enumerate packages explicitly (`platrixa`, `backend.kernel`, `backend.model_provider`, `backend.rules`, `backend.persistence`, required `backend.maths` modules) — never a blanket include |
| Importing `platrixa` becomes heavy (regression vs Kernel's lazy discipline) | Low | CI guard: `import platrixa` must not import torch/transformers |
| Developers use legacy entry points (`fyjc_orchestration`, Streamlit app) | Low | Docs explicitly mark them non-public; Phase 7I already classifies them test/legacy |

**Overall: additive, low-risk.** Zero production files change; the HTTP path, Kernel, providers, rules, and accounting are untouched.

---

## 10. Files that would need to change (future implementation — none touched now)

**New (all additive):**
- `platrixa/__init__.py`, `platrixa/_facade.py`, `platrixa/config.py`, `platrixa/__main__.py`
- `scripts/fte_fyjc_61_developer_interface_test.py`
- `docs/DEVELOPER_INTERFACE.md` (usage of §5–§7)
- optional `pyproject.toml` (only if installability is wanted; otherwise `PYTHONPATH=.` suffices, as all suites already do)

**Modified (minimal):**
- `README.md` (one section pointing at the developer interface) — optional

## 11. Files that should NOT change

`backend/kernel/kernel.py`, `backend/kernel/result.py`, `backend/model_provider/*`, `backend/rules/*`, `backend/persistence/*`, `backend/maths/*` (accounting/grounding/schema/runner), `api/*`, `frontend/*`, `hf_space/`, `training/*`, `requirements.txt` (no new deps needed — argparse is stdlib), all phase test suites 51–60, all locked Phase 6C data, Streamlit-era modules (still test-active per Phase 7I).

---

## 12. Explicit list of what Phase 11 must NOT implement yet

1. The `platrixa/` package itself (any module, any export).
2. The CLI / `__main__.py` / exit-code mapping.
3. `pyproject.toml` / packaging / PyPI publication.
4. Any new configuration env var, auth, billing, hosted service, or external API endpoint.
5. Any persistence option in the programmatic API (documented as deployment-level only).
6. Any result schema other than `KernelResult`/`to_dict()`.
7. Any test suite execution changes; existing suites 51–60 stay exactly as they are.
8. Any change to Kernel, providers, rules engine, grounding, schema, accounting semantics, model/adapter revisions, ZeroGPU duration, HF Space, Cloudflare, Render, PostgreSQL.
9. Repository reorganization, file moves, or legacy-module deletion (Phase 7I governance governs those).
10. A `docs/` rewrite beyond the one developer-interface page.

---

## 13. Current status summary

| Item | Status |
|---|---|
| Repository audit (11A) | **DONE** (this report) |
| Interface requirements (11B) | **DESIGNED** (§5–§7) |
| Authority rules (11C) | **DESIGNED** — facade is construct-and-configure only; re-export-only; guarded by planned static tests |
| Input/output contract (11D) | **DESIGNED** — reuse `raw_input` + `KernelResult` verbatim |
| Local vs remote (11E) | **DESIGNED** (§7 table) |
| Package design (11F) | **RECOMMENDED**: `platrixa/` thin facade at repo root |
| Test design (11G) | **DESIGNED** (suite 61 plan) |
| Implementation | **NOT STARTED** (per phase contract) |
| Commit / push | **NONE** |

**Audit verdict:** the runtime already contains every authority boundary the developer interface needs; the missing piece is purely a thin, re-export-only facade plus docs — the smallest clean surface that cannot become a second runtime because it contains no logic to become one.

*Files changed in this phase: this report only (new untracked artifact). Repository untouched.*
