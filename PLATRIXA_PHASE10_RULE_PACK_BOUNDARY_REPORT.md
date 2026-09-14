# PLATRIXA — PHASE 10: PROGRAMMABLE RULE-PACK BOUNDARY REPORT

**Date:** 2026-09-13 · **Mode:** architecture proof · **HEAD before changes:** `fcaefde`
**Verdict:** Phase 10 SUCCESS CRITERION **MET** — a developer can provide one declarative YAML rule pack and one programmatic Python hook without modifying Platrixa core runtime code; both execute through the same `RuleDecision` boundary; failures fail closed; evidence is observable; **neither can manufacture final VERIFIED**.

---

## 1. Executive summary

Platrixa now has a clean, minimal, production-quality extensibility boundary: developer rules (declarative YAML or Python hooks) execute **after** grounding and deterministic accounting produce a success state, and may only **downgrade** it (`VERIFIED → REVIEW_REQUIRED/BLOCKED`). There is no field, channel, or code path by which a rule can request, suggest, or manufacture `VERIFIED` — the contract type itself cannot express it (frozen `RuleDecision` has no status field; outcomes are only `PASS/FAIL/UNAVAILABLE/ERROR`; decision hints are load-time-restricted to `REVIEW_REQUIRED|BLOCKED`; hints are sanitized at evaluation). The boundary is **default-off**: with no rule pack configured, Kernel behavior is byte-identical (proven). 46/46 Phase 10 checks pass; the full regression suite (7B–7T, Phase 9, legacy persistence) is green.

This is an **architecture proof, not enterprise readiness** (known limitations in §15).

## 2. Before architecture

```
HTTP → FastAPI → Kernel.process()
                    ├─ provider.interpret()      (model — authority: interpretation only)
                    ├─ schema validation          (18-field contract)
                    ├─ grounding gate             (Rule 0: no VERIFIED claim; fail-closed)
                    ├─ deterministic accounting   (hardened_bookkeeping_outcome → flow_status)
                    └─ KernelResult               (final status = accounting flow status)
```

No developer extension point existed. Enterprises needing custom policies (reference required, high-value review, budget checks) would have to modify kernel/accounting code — violating the Phase 7 ownership model.

## 3. After architecture

```
HTTP → FastAPI → Kernel.process(rule_pack=..., rule_hooks=...)   ← optional, default OFF
                    ├─ provider.interpret()          (unchanged)
                    ├─ schema validation             (unchanged)
                    ├─ grounding gate                (unchanged — still blocks before rules see anything)
                    ├─ deterministic accounting      (unchanged — sole VERIFIED producer)
                    ├─ NEW: RuleEngine.evaluate()    (developer constraints, downgrade-only)
                    │     RuleContext (read-only view)
                    │     ├─ YAML rules (3 primitives, deterministic evaluators)
                    │     └─ custom RuleHooks (fail-closed guard)
                    │     → RuleDecision[] → RuleResult[] evidence
                    │     → state policy: downgrade VERIFIED → REVIEW_REQUIRED|BLOCKED
                    └─ KernelResult (status, rule_evidence in metadata channel)
```

Ownership: **AI interprets · grounding validates support · rule pack defines developer constraints · rule engine evaluates them · accounting kernel owns accounting truth · runtime/state machine owns final status · persistence stores results.** The rule system is *not* a second accounting engine: it performs no debit/credit/journal/ledger logic and cannot produce accounting output (proven: F10/F11).

## 4. Rule contract (IMPLEMENTED)

- **`RuleDecision`** — frozen dataclass: `rule_id: str`, `outcome: PASS|FAIL|UNAVAILABLE|ERROR`, `message: str`, `metadata: MappingProxy`. No status field exists. Construction-validated (`RuleContractError` on bad outcome/id). `is_blocking` = FAIL|UNAVAILABLE|ERROR.
- **`RuleContext`** — frozen, controlled read-only view: `request_id`, `raw_input`, `interpretation` (read-only mapping). Hooks cannot mutate kernel state.
- **`RuleHook`** — ABC: `rule_id: str` + `validate(context) -> RuleDecision`.
- **`RuleResult`** — evidence record (`rule_id, source, result, message, metadata`) via `decision_to_result`.
- Outcomes: `PASS` (continue), `FAIL` (constraint violated), `UNAVAILABLE` (external dependency down — **never** success), `ERROR` (rule itself failed — fail closed).

## 5. YAML example (IMPLEMENTED — `examples/rules/platrixa_rules.yaml`)

```yaml
rules:
  - id: invoice_reference_required
    type: required
    field: references
    decision: REVIEW_REQUIRED
    message: "This policy requires an invoice/reference on every transaction."

  - id: payment_method_allowed
    type: allowed_values
    field: payment_method_enum
    values: [CASH, CREDIT, BANK, CHEQUE, UPI, UNKNOWN]
    decision: REVIEW_REQUIRED

  - id: high_value_review
    type: threshold
    field: amounts.0.value
    operator: ">"
    value: 100000
    decision: REVIEW_REQUIRED
    message: "Transactions above Rs.1,00,000 require manual review under this policy."
```

Loaded via `Kernel(rule_pack="examples/rules/platrixa_rules.yaml")`. Parser guarantees: `yaml.safe_load` only (no code execution), structural validation, unknown rule types / unknown decision hints (`VERIFIED` rejected at load) / malformed rules / duplicate ids / bad operators / non-numeric thresholds all raise `RulePackError` — **fail closed before any transaction** (Kernel construction refuses a malformed pack, G1).

## 6. Python hook example (IMPLEMENTED — `examples/rules/custom_hooks.py`)

`HolidayBudgetRule(RuleHook)` — rule_id `holiday_budget`: derives a deterministic month from `request_id`, consults `_MockBudgetProvider` (deterministic fake; December = budget freeze, monthly limit otherwise), returns `UNAVAILABLE` if the provider is unhealthy (never PASS), `FAIL` on freeze/over-budget, `PASS` otherwise. Injected via `Kernel(rule_hooks=[HolidayBudgetRule()])` — zero core-runtime modification.

## 7. RuleDecision contract (IMPLEMENTED — see §4)

Key design point: **authority-free by construction.** Test D11 proves `RuleDecision` has no `status/verdict/final_state` field; C6 proves `outcome="VERIFIED"` raises; D10 proves the engine's downgrade map contains only `REVIEW_REQUIRED|BLOCKED`; D5/D6 prove a hook smuggling `decision_hint: VERIFIED` in metadata gets sanitized to `REVIEW_REQUIRED` in both applied state and recorded evidence.

## 8. State-authority proof (PROVEN)

| Required test | Result | Evidence |
|---|---|---|
| Passing custom rule | VERIFIED preserved | D2 |
| Failing custom rule | downgraded to REVIEW_REQUIRED | D3, F7 |
| Rule requesting VERIFIED | rejected/sanitized → REVIEW_REQUIRED; evidence clean | D5, D6, C6, A4 |
| Dependency unavailable | UNAVAILABLE blocks VERIFIED (never success) | C3, D3-class policy |
| Rule raises exception | ERROR decision → REVIEW_REQUIRED (no crash, no success) | C4, F8, F9 |
| Multiple rules, one fails | deterministic aggregation, worst case wins | D8, D9 |
| PASS rule on downgraded state | cannot upgrade back to VERIFIED | D7 |
| Structural: no VERIFIED path | downgrade map excludes it; frozen type can't express it | D10, D11 |

Rules also run **only** on accounting-produced success states — they can never repair a failure or manufacture a success (kernel seam position, §3). Phase 9 status normalization untouched (59-suite 20/20; A3/A4 passthrough proofs stand).

## 9. Fail-closed proof (PROVEN)

Malformed YAML → `RulePackError` at load (A2) and at Kernel construction (G1) · unknown rule type (A3) · unknown/forbidden decision hint (A4) · missing id (A5) · hook exception → ERROR (C4) · hook returns non-decision → ERROR (C5) · dependency unavailable → UNAVAILABLE (C3) · grounding failures not masked by rules (F12: injected bad party still → GROUNDING_FAILED with rules configured).

## 10. Evidence / auditability (IMPLEMENTED)

Every executed rule emits `{rule_id, source: "yaml"|"python_hook", result: PASS|FAIL|UNAVAILABLE|ERROR, message, metadata}`; `KernelResult.rule_evidence` carries the full list (and `to_dict()` includes it for API/persistence consumption). A developer can answer *"why was this rejected?"* directly from evidence (E4): e.g. `[{'rule_id': 'cap_rule', 'result': 'FAIL', 'message': 'amounts.0.value=25000.0 violates amounts.0.value < 10000'}]`. No secrets pass through the boundary (RuleContext exposes only request_id/raw_input/interpretation).

## 11. Test results — `scripts/fte_fyjc_60_rule_pack_boundary_test.py`: **46/46 PASS**

A. YAML loading/validation 5/5 · B. primitives 6/6 · C. hooks 6/6 · D. state authority 11/11 · E. evidence 4/4 · F. kernel integration 12/12 · G. construction fail-closed 2/2. Integration proofs (10I): Scenario A — same input, pack A → REVIEW_REQUIRED with 3 evidence records, no pack → VERIFIED (**config-only behavior change, zero core-code change**); Scenario B — `HolidayBudgetRule` PASS→VERIFIED preserved, December-freeze FAIL→REVIEW_REQUIRED with evidence.

## 12. Regression results (10J) — all green

| Suite | Result |
|---|---|
| Phase 10 (`fte_fyjc_60`) | 46/46 |
| Phase 7B (`51`) | 13/13 |
| Phase 7C (`52`) | 20/20 |
| Phase 7D (`53`) | PASS |
| Phase 7E (`54`) | 80/80 |
| Phase 7F (`55`, sweeps 7B–7E) | 61/61 |
| Phase 7G (`58_ui`) | 46/46 |
| Phase 7H (`56`) | 40/40 |
| Phase 7R (`57`) | 43/43 |
| Phase 7T transport (`58_hf`) | 49/49 |
| Phase 9 (`59`) | 20/20 |
| Legacy persistence (`fyjc_db_persistence_test`) | 18/18 OK |

One regression interaction was caught and resolved: the 7C static architecture guard (`no "st." in kernel executable code`) rejected a new comment ending in "request." — reworded the comment; **no test was modified**. Locked Phase 6C data untouched; no model/Space/ZeroGPU/provider changes.

## 13. Files changed

**Modified (tracked):**
- `backend/kernel/kernel.py` (+57): optional `rule_pack`/`rule_hooks` constructor params (construction-time fail-closed), lazy `_get_rule_engine()`, Phase 10 seam at the terminal edge of `process()` (downgrade-only), `KernelResult.rule_evidence` field + `to_dict()` key.
- `requirements.txt` (+2): `pyyaml>=6.0` (present in env at 6.0.3; loader fails closed without it).

**New (untracked):**
- `backend/rules/__init__.py`, `backend/rules/contract.py`, `backend/rules/loader.py`, `backend/rules/engine.py`
- `examples/rules/platrixa_rules.yaml`, `examples/rules/custom_hooks.py` (outside core runtime, deliberately)
- `scripts/fte_fyjc_60_rule_pack_boundary_test.py`
- `PLATRIXA_PHASE10_RULE_PACK_BOUNDARY_REPORT.md` (this report)

## 14. Files deliberately untouched

Grounding gate (`fyjc_grounding_gate.py`) · accounting (`fyjc_accounting.py`, `hardened_bookkeeping_outcome`) · `backend/model_provider/*` (Phase 9 seam intact) · `backend/persistence/*` · `api/*` · schema (`schema_verifier.py`) · 18-field contract · `training/*` incl. locked test set (SHA `c1243723…` verified intact) · `hf_space/` (not added to git) · Cloudflare/Render config · ZeroGPU duration · all 44 pre-existing untracked artifacts.

## 15. Known limitations (deliberate, proof-scoped)

1. Rule context is read-only over the interpretation — no ledger/aggregates access (by design: prevents a second accounting engine).
2. YAML primitives are exactly three (`required`, `allowed_values`, `threshold`); no conditions/composition/negation yet.
3. Hook resolution is in-process only — no remote rule-service protocol, no isolation sandboxing of hook code.
4. Evidence is returned on the result; no dedicated persistence table or rule-pack versioning/registry yet.
5. Threshold uses the first amount only (`amounts.0.value`); multi-amount policies need future primitives.
6. Rule packs are not hot-reloaded; they bind at Kernel construction.
7. The kernel-level test used a stub provider (deterministic); live-model rule execution was not quota-spent (ZeroGPU pool exhausted until ~2026-09-14T04:30Z; rules run *after* the model, so model identity cannot affect rule behavior).

## 16. Future extension path

More declarative primitives (conditional/composite rules, date windows, party lists) → rule-pack versioning + registry + per-tenant packs → evidence persistence table (Phase 7E boundary) → hook isolation (subprocess/WASM) → remote rule-service transport → optional API exposure of `rule_evidence`. Each step extends the same `RuleDecision` boundary without touching the authority invariant.

---

**Status classification:** contract IMPLEMENTED · YAML pack IMPLEMENTED · hook protocol IMPLEMENTED · engine + downgrade policy IMPLEMENTED · evidence IMPLEMENTED · state authority PROVEN · fail-closed PROVEN · default-off byte-identity PROVEN · integration scenarios PROVEN · enterprise-readiness NOT claimed (see §15).
