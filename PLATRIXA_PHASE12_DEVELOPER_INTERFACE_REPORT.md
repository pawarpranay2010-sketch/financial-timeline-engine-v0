# PLATRIXA — PHASE 12: DEVELOPER INTERFACE REPORT

**Status: PASS**

The public developer interface is implemented, proven, and regression-clean.
A developer can now answer "How do I use Platrixa?" with: import one clean
interface, submit financial input, get a stable result + evidence — without
ever touching the accounting engine, grounding, provider, RuleEngine, or
persistence internals.

---

## 1. Audit findings

Audited before any code (facts verified against source, not assumed):

| Item | Finding |
|---|---|
| Kernel constructor | `Kernel(model_provider=None, provider_config=None, rule_pack=None, rule_hooks=None)` — already configuration-shaped (Phase 7C + Phase 10 seam) |
| `Kernel.process` signature | `process(raw_input: str, *, request_id: Optional[str]) -> KernelResult`; empty input → `VALIDATION_FAILED` result (kernel-side fail-closed) |
| Input contract | raw transaction string; HTTP schema enforces 1–2000 chars (`KernelProcessRequest.raw_input`) — the only cleanly supported input form |
| `KernelResult` | `request_id, raw_input, status, status_label, success, interpretation_candidate, accounting_result, verification_status, grounding_issues, issues, next_action, metadata, rule_evidence` + `to_dict()`; `backend/kernel/result.py` is the declared public import surface |
| Configuration model | frozen `ProviderConfig` (pinned base `989aa798…` / adapter `b5c0a37…` revisions) + `PLATRIXA_*` env family + `PLATRIXA_MODEL_TRANSPORT` |
| Provider abstraction | `ModelProvider` Protocol; `LocalHFModelProvider(config=None)`, `RemoteHFModelProvider(config=None, url=None, token=None, timeout=None)`, `HFGradioModelProvider` subclass; factory `get_model_provider` = single selection point |
| Rule interfaces | `RuleHook` ABC (`rule_id` + `validate(RuleContext) -> RuleDecision`), frozen `RuleDecision` (no status field; VERIFIED inexpressible), `RuleEngine.evaluate` downgrade-only |
| Exceptions | `ModelProviderError` hierarchy (`ModelUnavailableError`, `MalformedOutputError`, `ForbiddenAccountingFieldError`, `GenerationError`, `ValidationError`); `RulePackError`/`RuleContractError` |
| Packaging | no `pyproject.toml`, no version, no CLI — repo not importable without `sys.path` work (the gap this phase closes) |
| Existing public boundary | `backend/kernel/result.py` (declared), `examples/rules/` (Phase 10), HTTP API `POST /api/v1/kernel/process` |

## 2. Chosen package structure

```
platrixa/
    __init__.py     # public exports + __version__ (0.1.0); cheap import
    config.py       # frozen PlatrixaConfig (validates at client construction)
    errors.py       # PlatrixaError / InputError / ProviderError
    _facade.py      # Platrixa client + PlatrixaResult projection
    __main__.py     # CLI: `python -m platrixa process ...`
```

Deliberately small public API: `Platrixa`, `PlatrixaResult`, `PlatrixaConfig`,
`PlatrixaError`/`InputError`/`ProviderError`, status constants re-exported
from the Kernel's public contract, `__version__`. No backend module is
re-exported wholesale.

## 3. Public API

```python
from platrixa import Platrixa, PlatrixaConfig

client = Platrixa()                                   # auto provider selection
result = client.process("Purchased furniture for cash ₹15,000")
result.status            # VERIFIED / REVIEW_REQUIRED / BLOCKED / ...
result.interpretation    # 18-field candidate
result.accounting        # deterministic accounting result
result.rule_evidence     # structured rule evidence (Phase 10)
result.to_dict()         # deterministic JSON-safe serialization

cfg = PlatrixaConfig(provider="remote", transport="gradio",
                     rule_pack="examples/rules/platrixa_rules.yaml",
                     rule_hooks=[MyHook()])
client = Platrixa(config=cfg)
```

Guarantees (proven in §9): `process` forwards to `Kernel.process` exactly
once; the facade contains no provider/accounting/grounding/rule/persistence
logic; VERIFIED is never recomputed or injectable.

## 4. CLI

```
python -m platrixa process --text "..." [--provider auto|local|remote]
python -m platrixa process input.json [--rules pack.yaml] [--hook module:Class] [--pretty]
python -m platrixa --version
```

Machine-readable JSON output (`result.to_dict()`); deterministic exit codes
`0=VERIFIED, 1=REVIEW_REQUIRED, 2=BLOCKED, 3=fail-closed failure/error`.
No shell/daemon/auth/server/wizard. The CLI imports `Platrixa` from the
public package at call time — it is a consumer of the API, not a second path.

## 5. Configuration

`PlatrixaConfig` (frozen dataclass, constructor-validated, no global state):
`provider` (`auto`/`local`/`remote` — `auto` defers to the Kernel's existing
env-driven selection point), `endpoint_url`/`endpoint_token`/`timeout`/
`transport` (existing remote mechanisms; `None` = env defaults),
`provider_config` (optional `ProviderConfig`; default keeps pinned
revisions), `rule_pack` (YAML path; malformed pack fails client
construction), `rule_hooks` (validated non-empty `rule_id` at construction).
No new env vars, no API keys, no auth.

## 6. Result & error contract

- **Result:** `PlatrixaResult` is a read-only projection of the authoritative
  `KernelResult` (view, not recompute; `kernel_result` escape hatch exposes
  the underlying object — one runtime, no shadow). `to_dict()` is
  JSON-safe: the one projection rule is `Decimal → exact string`
  (deterministic, precise); datetime → ISO; sets → sorted lists.
- **Errors:** `InputError` (non-string/empty/>2000 chars — raised before the
  Kernel runs); `ProviderError` (wraps provider runtime failures with
  `__cause__` preserved); `ValueError`/`RulePackError` at construction
  (fail-closed, unwrapped). All operational failures remain fail-closed
  result states (MODEL_UNAVAILABLE, VALIDATION_FAILED, GROUNDING_FAILED,
  FORBIDDEN_OUTPUT, UNSUPPORTED_TRANSACTION, BLOCKED). Nothing is converted
  into success; nothing is silently swallowed.

## 7. Documentation & examples

- **`docs/DEVELOPER_INTERFACE.md`** — all 12 required sections, states the
  thin-boundary principle, makes no enterprise/SaaS/auth claims.
- **`examples/developer_interface/`** — `basic_usage.py`, `rulepack_usage.py`
  (reuses Phase 10 YAML pack), `rulehook_usage.py` (reuses Phase 10
  `HolidayBudgetRule`), `cli_usage.py` + `transaction.json`.

## 8. Security

- Credential scan of `platrixa/` + `examples/developer_interface/`: clean
  (no tokens, keys, passwords, DB URLs).
- The facade itself reads no environment variables — secrets stay at the
  provider layer via existing env mechanisms.
- CLI/JSON output verified to contain no credentials, reprs, or stack traces;
  the fail-closed provider output exposes only model identity + reason.
- No authentication added (per phase contract).

## 9. Tests — Phase 12 suite (`scripts/fte_fyjc_61_developer_interface_test.py`)

**65/65 PASS**, covering A–N plus the Kernel routing proof:

- **A** package imports; exports complete (`__version__=0.1.0`)
- **B** fresh-process import with empty env → no torch/transformers, no network
- **C** documented shape (signature, result properties, config fields, no global state)
- **D** **Kernel routing proof:** `client.process` → `Kernel.process` **exactly
  once** (counting wrapper; provider `interpret` also exactly once, only via
  the Kernel; second call → second single call)
- **E** **no-bypass proof:** static scan — facade contains zero references to
  accounting engine, grounding gate, schema validator, RuleEngine, persistence,
  or direct `.interpret(`; exactly one `self._kernel.process(` delegation;
  holds a real `Kernel` (no shadow runtime)
- **F** CLI→public-API proof (spy patches the package attribute; CLI invoked
  `Platrixa` exactly once; exit-code map verified)
- **G** deterministic JSON (identical inputs → byte-identical output; JSON-safe;
  no `0x` reprs; no credentials)
- **H** RulePack via public API (example pack → 3 evidence records; threshold
  pack downgrades VERIFIED→REVIEW_REQUIRED with FAIL evidence; malformed pack
  fails client construction)
- **I** RuleHooks via public API (PASS preserves VERIFIED + evidence; FAIL
  downgrades; non-hook object rejected at construction)
- **J** VERIFIED-smuggle sanitized: metadata-based hint rejected and recorded
  (`decision_hint_rejected: VERIFIED`), state unchanged; kwarg-based smuggle
  fails closed (contract error → ERROR decision → downgrade)
- **K** PASS cannot upgrade a downgraded state (REVIEW_REQUIRED preserved)
- **L** UNAVAILABLE/ERROR hooks never become success
- **M** facade parity with direct `Kernel()` (status + accounting identical;
  empty-input contract preserved)
- **N** Phase 10 invariants intact (`RuleDecision` cannot express VERIFIED;
  RuleEngine stays kernel-side)

Two development-time fixture bugs were caught and fixed **in the tests**
(threshold-operator semantics; smuggle-fixture contract error) — no product
change was bent to satisfy a test. One genuine product improvement came from
testing: `to_dict()` needed explicit Decimal handling (the HTTP layer's
FastAPI encoder had masked this).

## 10. Regression results (all green, old tests unmodified)

| Suite | Result |
|---|---|
| 7B model provider (51) | PASS (13 checks) |
| 7C kernel boundary (52) | PASS (20 checks) |
| 7D grounding wiring (53) | PASS |
| 7E persistence (54) | 80/80 |
| 7F FastAPI boundary (55) | 61/61 |
| 7G UI boundary (58_ui) | 46/46 |
| 7H cold-start seam (56) | 40/40 |
| 7R remote provider (57) | 43/43 |
| 7T HF-Gradio transport (58_hf) | 49/49 |
| Phase 9 status contract (59) | 20/20 |
| Phase 10 rule boundary (60) | 46/46 |
| Legacy persistence (`backend.fyjc_db_persistence_test`) | 18/18 OK |
| **Phase 12 developer interface (61)** | **65/65** |

**Total: 501 checks, 0 failures.** No old test was modified to pass.

## 11. Import / package quality

- No circular imports (probe: `platrixa` → backend modules → `platrixa.__main__`).
- `import platrixa` is cheap: 0.26s, no torch/transformers/model download/
  network/DB init (Kernel's lazy discipline preserved; heavy imports deferred
  to `_build_kernel`/process time).
- Clean type hints and docstrings throughout; deliberately small surface;
  py_compile clean on all new files.

## 12. Files changed (all new — zero modifications to existing files)

| File | Purpose |
|---|---|
| `platrixa/__init__.py` | public exports + version |
| `platrixa/config.py` | `PlatrixaConfig` |
| `platrixa/errors.py` | public error contract |
| `platrixa/_facade.py` | `Platrixa` client + `PlatrixaResult` |
| `platrixa/__main__.py` | CLI |
| `docs/DEVELOPER_INTERFACE.md` | developer documentation |
| `examples/developer_interface/*` | 4 examples + transaction JSON |
| `scripts/fte_fyjc_61_developer_interface_test.py` | Phase 12 evidence suite |
| `PLATRIXA_PHASE12_DEVELOPER_INTERFACE_REPORT.md` | this report |

**Files intentionally untouched:** `backend/kernel/*`, `backend/model_provider/*`,
`backend/rules/*`, `backend/persistence/*`, `backend/maths/*`, `api/*`,
`frontend/*`, `hf_space/`, `training/*`, `requirements.txt` (no new deps —
CLI uses stdlib argparse), all suites 51–60, all locked Phase 6C data,
Streamlit-era modules.

## 13. Limitations

- No persistence from the programmatic interface (deliberate: persistence is
  deployment-level via the HTTP boundary).
- No async API, no batch processing, no streaming.
- Local inference downloads/loads the pinned model on first use (unchanged
  Kernel behavior).
- `pyproject.toml`/PyPI packaging intentionally deferred — the package works
  from the repository root today; packaging is a future decision.
- HTTP service, auth, billing, hosted platform: future phases (per contract).

## 14. Security limitations

- No authentication/authorization on the interface (per phase contract).
- Endpoint tokens remain operator-supplied via existing env/config; they are
  never echoed in results, CLI output, or errors.
- The interface inherits the runtime's fail-closed guarantees; it adds no
  sandboxing around developer hooks beyond the existing contract (hooks are
  trusted code, but their failures still cannot manufacture success).

## 15. Git status

- HEAD: `9729069` (`feat(fyjc): add Phase 10 rule-pack boundary...`)
- Tracked modifications: **0** (`git diff --stat` empty)
- New untracked: `platrixa/`, `docs/DEVELOPER_INTERFACE.md`,
  `examples/developer_interface/`, `scripts/fte_fyjc_61_...py`, this report,
  plus the pre-existing `PLATRIXA_PHASE11_DEVELOPER_INTERFACE_AUDIT.md`
- All pre-existing untracked artifacts (44) untouched; `hf_space/` not added
- **No commit, no push** (per Git Safety rules)

## 16. Kernel routing proof (summary)

```
client.process(input)
    → InputError guard (shape only)
    → self._kernel.process(text, request_id=…)   ← exactly one call site
        → Kernel.process                          ← counted: exactly once
            → provider.interpret                  ← counted: exactly once, Kernel-only
            → schema → grounding → accounting → rules (all Kernel-internal)
    → PlatrixaResult(kernel_result)               ← view, no recompute
```

Static proof: the facade's five modules contain zero references to the
accounting engine, grounding gate, schema validator, RuleEngine, or
persistence, and exactly one `self._kernel.process(` delegation point.

---

**PHASE 12 STATUS: PASS**
