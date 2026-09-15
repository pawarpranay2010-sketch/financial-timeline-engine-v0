# PLATRIXA — PHASE 17 ARCHITECTURE REPORT

**Date:** 2026-09-15
**Phase:** 17 — Architecture Hardening
**Classification:** ✅ **PASS** (architecture boundary implemented and proven;
the ML-evidence half is reported separately in
`PLATRIXA_PHASE17_ML_EVIDENCE_REPORT.md` — the model *run* itself is
infrastructure-blocked in this environment, documented honestly there)
**Evidence suites:** `scripts/fte_fyjc_67_phase17_boundary_test.py` (**48/48**),
`scripts/fte_fyjc_68_phase17_rules_benchmark_test.py` (**51/51**)

---

## 1. Architecture BEFORE Phase 17 (audited, not assumed)

The live execution path in `backend/kernel/kernel.py` (Phase 7C/7R wiring):

```
raw_input
  → ModelProvider.interpret()                (candidate dict, 18-field contract)
  → StructuredInterpretationValidator        (schema validation)
  → ExpandedGroundingGate.ground()           (deterministic grounding)
  → process_accounting(candidate, raw_input) (existing hardened flow)
  → RuleEngine.evaluate() [optional]         (downgrade-only, Phase 10)
  → KernelResult(status)                     (Kernel owns final state)
```

### Weaknesses discovered (each proved by code reference)

**W1 — accounting consumed the RAW candidate dict (not a grounded type).**
`Kernel.process()` passed `candidate` (the model's raw dict) to
`process_accounting(candidate, raw_input)` (kernel.py §5). Grounding had
passed a moment earlier, but the *type* crossing the accounting boundary was
the untyped candidate — nothing structurally distinguished grounded from
ungrounded at the call site. `_best_amount()` even re-verified amounts against
`raw_input` with substring search, duplicating (weaker) grounding inside the
accounting delegation.

**W2 — no Candidate/Grounded type distinction anywhere.**
The grounding gate returned `GroundingResult` (a verdict), but the object
flowing onward remained the same dict the model produced. `accounting(candidate)`
was a legal (and normal) call; nothing prevented `accounting(ungrounded_dict)`.

**W3 — evidence did not bind the execution.**
`KernelResult` carried `metadata={"model_id", "provider_revision"}` only. No
input hash, no interpretation hashes, no prompt/schema/grounding/accounting
versions, no rule-pack hash. §7/§8 of the phase contract were unmet.

**W4 — benchmark correctness was field-level only.**
The 53-case P5A benchmark (`scripts/fte_fyjc_p5a_evaluation.py`) scores
field-level accuracy; a model can score well per-field while composing a wrong
transaction. No whole-transaction metric, no adversarial/counterfactual
methodology, and the dataset was not locked against training overlap.

### Non-weaknesses verified (already correct, unchanged)

- **Model cannot claim VERIFIED** — the grounding gate rejects a VERIFIED
  `suggested_status` (fail-closed → `GROUNDING_FAILED`), and the provider
  boundary rejects forbidden accounting fields.
- **RuleEngine is downgrade-only** (Phase 10) — `RuleDecision` has no status
  field; loader rejects `VERIFIED` hints at load; `_sanitize_hint` neutralizes
  smuggled hints; `_apply_decision` can only move states down.
- **Kernel owns final state** — `KernelResult.success` derives solely from
  `Kernel.status`; accounting flow status is propagated, never upgraded.
- **Rules default off** — byte-identical behavior without a pack.

## 2. Architecture AFTER Phase 17

```
raw_input
  → ModelProvider.interpret()
  → CandidateSemanticIR            ← typed CANDIDATE (new, backend/semantics/)
  → StructuredInterpretationValidator
  → ExpandedGroundingGate.ground() (deterministic — the ONLY constructor
  → GroundedSemanticIR               authority for GroundedSemanticIR)
  → process_accounting(grounded_ir) ← accepts ONLY GroundedSemanticIR
  → RuleEngine.evaluate() [optional, downgrade-only]
  → KernelResult + ExecutionEvidence ← versioned execution chain (new)
```

## 3. CandidateSemanticIR / GroundedSemanticIR

New package `backend/semantics/` (`ir.py`, `evidence.py`). Deliberately NOT a
second schema: both types are frozen views over the existing 18-field
candidate contract.

**CandidateSemanticIR** — what the model *believes* the input means:
- frozen, validated at construction; raises `SemanticIRError` on any
  forbidden accounting-truth field (`journal`, `debit_lines`, …);
- has NO `for_accounting()` — no path to accounting exists on the type;
- `content_digest` = sha256 of canonical JSON of `{raw_input, fields}`
  (hash definition documented in-code; model identity deliberately excluded
  and recorded separately).

**GroundedSemanticIR** — only gate-verified claims:
- **the only public constructor is `GroundedSemanticIR.from_candidate(
  candidate, grounding_result)`**, which requires a PASSING
  `GroundingResult` (`safe_for_kernel` AND `grounded`); direct construction
  raises `SemanticIRError`; a failing result cannot manufacture one;
- `for_accounting()` is the sole accounting admission payload
  (`raw_input` + the six groundable semantic fields; no bookkeeping noise);
- `grounding_version` is stamped on the instance and into its digest.

## 4. Grounding boundary (unchanged implementation, real authority)

`ExpandedGroundingGate` remains THE deterministic grounding implementation —
Phase 17 did not touch or duplicate it. Phase 17 made its verdict a
*structural requirement*: a `GroundedSemanticIR` cannot exist without it.
Grounding still answers only "is this claim supported by the source?" and
still fails closed (`do not manufacture support; do not silently fill`).

## 5. Accounting boundary

`Kernel.process_accounting(grounded)`:
- accepts **only** `GroundedSemanticIR` — a `CandidateSemanticIR`, raw dict,
  or any other type raises `SemanticIRError` at the boundary;
- `accounting(candidate_ir)` is no longer an accepted normal execution path;
- the existing deterministic flow (`hardened_bookkeeping_outcome` →
  `orchestrate`) is **unchanged** — no second engine, no rewritten logic;
- the redundant substring re-verification of amounts was removed from the
  delegation: every amount crossing this boundary was already
  deterministically grounded by the gate (the re-check was a weaker copy of
  grounding living in the wrong layer).

## 6. RuleEngine boundary (Phase 10 invariant preserved, proven again)

No rule-code changes. New proofs in `fte_fyjc_67` §5–§6 and `fte_fyjc_68`
§11–§15: downgrade works; no PASS/hook/pack path upgrades anything; a hook
smuggling `VERIFIED` in `decision_hint` has the hint neutralized and the
rejected value recorded; malformed packs fail closed at load; hook
exceptions and wrong return types become ERROR decisions.

## 7. Kernel final-state authority

Unchanged in code, now proven with live execution tests (§21 A–F):
- model suggesting VERIFIED → grounding gate fails closed →
  `GROUNDING_FAILED` (the pipeline never continues on the model's claimed
  state); VERIFIED appears only as a *rejected claim* in issues;
- `KernelResult.success` derives only from `Kernel.status`;
- grounding failure + VERIFIED suggestion ≠ VERIFIED.

## 8. Evidence chain (`backend/semantics/evidence.py`)

`ExecutionEvidence` (frozen, `exec-evidence-1`) — every hash has a documented
definition; nothing unmeasurable is claimed; empty when genuinely absent
(never invented):

| field | definition |
|-------|------------|
| `input_hash` | sha256(raw_input UTF-8) |
| `candidate_interpretation_hash` | `CandidateSemanticIR.content_digest` |
| `grounded_interpretation_hash` | `GroundedSemanticIR.content_digest` |
| `model_identity` | provider-reported `{model_id, provider_revision}` |
| `adapter_identity` | configured `{adapter_repo_id, adapter_revision}` |
| `schema_version` | `sem-ir-1` |
| `prompt_version` | `prompt:sha256:<16hex>` of the prompt template the runtime actually executes; empty when the provider manages its own default (never an invented hash) |
| `grounding_version` | `expanded-gate-1` (the actual gate) |
| `accounting_version` | `hardened-bk-15i` (the actual flow) |
| `rule_pack_hash` | sha256 of the pack file BYTES; empty when no pack / unreadable |
| `rule_evidence` | the `RuleResult` records the runtime actually produced |
| `final_state` | the state the Kernel actually returned |

§8 non-cosmetic proof: tests assert `recorded evidence == actual execution`
— hashes recomputed from the live IRs, model identity captured from the
provider stub that really executed, pack hash from the file really loaded,
final state from the result really returned, determinism across equivalent
runs, divergence across different inputs. No secrets in evidence (asserted).

## 9. rules.md design

`rules.md` (repo root) is the developer-facing authoring spec, written
strictly against the existing Phase 10 primitives (`required`,
`allowed_values`, `threshold`; decision ∈ {REVIEW_REQUIRED, BLOCKED}).
Covers all twelve mandated sections, including bad-vs-good (a ~100-line hook
collapsed to a 7-line YAML rule) and the complexity principle: *use the
smallest rule representation capable of expressing the constraint*. No new
DSL, no new primitives.

## 10. Tests proving the boundaries

`scripts/fte_fyjc_67_phase17_boundary_test.py` — 48 checks:
candidate typing; grounding→grounded construction; accounting rejects
candidate/dict; ungrounded cannot reach accounting; rule downgrade; no
upgrade path (3 smuggling vectors + pack-load rejection); Kernel authority;
evidence binding (14 checks); pack-hash determinism; version identifiers.

`scripts/fte_fyjc_68_phase17_rules_benchmark_test.py` — 51 checks:
declarative authoring; YAML decisions; malformed fail-closed (7 shapes);
VERIFIED-request hook impossible; complex hooks; dataset lock; hash
determinism; overlap detection (planted-collision self-test); metric
determinism; counterfactual pair integrity; compositional ≠ field.

## 11. Migration notes

- `Kernel.process_accounting` signature changed from
  `(candidate: dict, raw_input: str)` to `(grounded: GroundedSemanticIR)`.
  In-repo callers: only `Kernel.process` (updated). The Phase 54 source scan
  ("def process_accounting") still passes — the method is unchanged in name
  and remains absent from persistence files.
- `KernelResult` gained `evidence`, `candidate_ir`, `grounded_ir` (all
  optional; old constructions still valid).
- `KernelResult.to_dict()` now includes `"evidence"`.
- `_best_description` / `_best_amount` now operate on the grounded payload.
- Public API (`platrixa`), HTTP routes, persistence: untouched.

## 12. Intentionally unchanged components

Model provider contract and implementations; schema validator; grounding
gate; the entire deterministic accounting stack (`fyjc_bk_reasoning`,
`fyjc_accounting`, `fyjc_orchestration`); rule contract/engine/loader;
persistence; FastAPI/developer API; all Phase 10–16 suites (all green).

## 13. Unresolved limitations

- `prompt_version` is empty when a provider manages its own default prompt
  internally (LocalModelRunner's fallback) — the runtime cannot measure what
  it does not see; recorded as empty rather than invented.
- Grounding remains source-containment based (as before); semantic paraphrase
  support is bounded by that design (unchanged scope).
- The Phase 17 ML evaluation could not execute the real model in this
  environment (2 GB RAM, no swap) — see the ML evidence report.
