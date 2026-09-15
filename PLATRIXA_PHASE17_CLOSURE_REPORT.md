# PLATRIXA — PHASE 17 CLOSURE REPORT

**Date:** 2026-09-15
**Phase:** 17 — Architecture Hardening + Independent ML Evidence
**Classification:** ✅ **PASS (architecture) / ⚠️ PARTIAL (ML execution) — overall PARTIAL**
**Commit:** created during this closure on `main` —
message `phase 17: harden audit chain — semantic IR boundary, evidence chain, locked 92-case benchmark`.
Exact hash recorded in the Phase 17/18 closure summary and `git log -1`.
**Push:** pushed to `origin/main` during closure (result verified there).

Per the phase contract §25: PASS required the Qwen+LoRA baseline to be
evaluated. The real model run is infrastructure-blocked (§4). The
architecture half is fully implemented, proven, and regression-green.

---

## 1. Verification performed at closure (exact results)

| Verification | Result |
|---|---|
| `scripts/fte_fyjc_67_phase17_boundary_test.py` | **PASS — 48/48** (fresh run at closure) |
| `scripts/fte_fyjc_68_phase17_rules_benchmark_test.py` | **PASS — 51/51** (fresh run at closure) |
| Locked benchmark case count | **92** records |
| Locked benchmark hash (fresh `sha256sum`) | `af1ab919f6fcb196260652b5af925f5addc0f6d5117813d8e0472ddaa0281ba1` |
| Harness `dataset_sha256()` (lock fingerprint) | `af1ab919…281ba1` — **matches the file bytes** |
| Dataset version | `phase17-benchmark-v1` |

**Closure finding — stale hash in the ML report (corrected in this commit):**
the ML evidence report printed `f2612e23…`, which did not match the locked
file bytes. Evidence trail: the dataset's mtime (09:10:40) precedes the
report's mtime (09:17:18), and the same report documents the final two-case
rewording (C0004/C0008 → Deshpande/Iyer) that produced the current bytes —
i.e. the report was written after the final edit but quoted a hash captured
before it. The **lock itself was never violated**: the harness lock
(`dataset_sha256()` = sha256 of exact file bytes) matches the file that
executed, and fte_fyjc_68 validates against it. The stale hash existed only
in prose. Corrected in `PLATRIXA_PHASE17_ML_EVIDENCE_REPORT.md` during
closure; no dataset byte was touched.

## 2. What was IMPLEMENTED (shipped in this commit)

- **Semantic IR boundary** — `backend/semantics/`:
  `CandidateSemanticIR` (frozen model-proposal view, rejects
  accounting-truth fields, no `for_accounting()`) and
  `GroundedSemanticIR` (sole constructor
  `from_candidate(candidate, grounding_result)` requiring a PASSING gate
  result; `for_accounting()` is the only admission payload into
  accounting).
- **Kernel hardening** — `backend/kernel/kernel.py` (+160/−32): the
  accounting boundary now accepts **only** `GroundedSemanticIR` (anything
  else raises); the redundant substring amount re-check was removed (it was
  a weaker copy of grounding in the wrong layer); the deterministic flow is
  otherwise byte-unchanged.
- **ExecutionEvidence chain** — `backend/semantics/evidence.py`
  (`exec-evidence-1`): input/candidate/grounded hashes (each with a
  documented exact hash definition), model/adapter identity captured from
  the objects that actually executed, schema/prompt/grounding/accounting
  versions, sha256 of the rule-pack file bytes, rule evidence, actual
  final state. Non-cosmetic (§8): fte_67 recomputes every hash from live
  objects; empty (never invented) when unmeasurable; no secrets.
- **`rules.md`** — developer rule-authoring spec on the existing Phase 10
  primitives (no new DSL): all 12 mandated sections, bad-vs-good example
  (100-line hook → 7-line YAML), complexity principle.
- **Independent locked benchmark** — `training/phase17_benchmark.jsonl`
  (92 cases) + `training/phase17_benchmark.py` (harness through the
  production validator + grounding gate; compositional headline metric;
  field metrics diagnostic; overlap check against 14 training corpora).
- **Test suites** — `scripts/fte_fyjc_67_phase17_boundary_test.py` (48
  checks), `scripts/fte_fyjc_68_phase17_rules_benchmark_test.py` (51
  checks).
- **Reports** — `PLATRIXA_PHASE17_ARCHITECTURE_REPORT.md`,
  `PLATRIXA_PHASE17_ML_EVIDENCE_REPORT.md` (hash corrected at closure).

## 3. What was PROVEN

- Model output is Candidate IR; grounding creates Grounded IR; accounting
  accepts only Grounded IR; ungrounded IR cannot reach accounting
  (fte_67 §1–4).
- RuleEngine can downgrade and cannot upgrade to VERIFIED; hook
  `suggested_status="VERIFIED"` is fail-closed to GROUNDING_FAILED by the
  gate (fte_67 §5–7A).
- Kernel owns final state; evidence binds actual execution; RulePack hash
  deterministic; version identifiers captured (fte_67 §8–10).
- YAML rules produce expected decisions; malformed rules fail closed;
  hooks cannot mint VERIFIED (fte_68 §11–15).
- Benchmark locked, hash-deterministic, train/eval-overlap-checked,
  whole-transaction metric deterministic, counterfactual pair integrity
  12/12, field accuracy provably cannot substitute for compositional
  accuracy (fte_68 §16–21).
- **Regressions:** fte_52, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 65, 66
  — all green (run during Phase 17 implementation; fte_68 re-run green at
  closure).

## 4. What was BLOCKED (infrastructure)

**The real 92-case Qwen+LoRA execution did not run.** Measured host:
2.0 GB RAM, **no swap**, disk 100% full — Qwen2.5-1.5B fp16 weights are
~3.1 GB. Weights load (338/338 shards), then generation is OOM-killed.
Per §2/§22 discipline the run was not faked, retried into pretending,
sampled, or substituted: **no model-quality numbers exist from Phase 17.**
This blocker is carried into Phase 18.

## 5. NOT YET PROVEN (honest ledger)

| Item | Status |
|---|---|
| Real 92-case model execution | ❌ not run (§4 blocker) |
| Compositional metrics from real executions | ❌ no data (blocked by above) |
| ExecutionEvidence from real model executions | ❌ (blocked by above) |
| **Permanent ExecutionEvidence persistence (Railway PostgreSQL)** | ❌ Phase 18 scope |
| Evidence retrieval + integrity re-verification from PostgreSQL | ❌ Phase 18 scope |

ExecutionEvidence is currently **in-memory only** (constructed by the
Kernel, bound into results and API responses; nothing persists it).

## 6. Git capture (this commit)

Staged (exactly the 9 Phase 17 files):

```
M  backend/kernel/kernel.py
A  PLATRIXA_PHASE17_ARCHITECTURE_REPORT.md
A  PLATRIXA_PHASE17_ML_EVIDENCE_REPORT.md
A  PLATRIXA_PHASE17_CLOSURE_REPORT.md
A  backend/semantics/__init__.py
A  backend/semantics/evidence.py
A  backend/semantics/ir.py
A  rules.md
A  scripts/fte_fyjc_67_phase17_boundary_test.py
A  scripts/fte_fyjc_68_phase17_rules_benchmark_test.py
A  training/phase17_benchmark.jsonl
A  training/phase17_benchmark.py
```

Explicitly **excluded**: the pre-existing untracked report/sprint files,
`hf_space/`, `content_bank/`, `training_data/`, `backend/maths/fyjc_ai_adapter.py`
(pre-existing untracked experiment, not part of the hardened boundary),
all `__pycache__/` (gitignored), and everything else from the 52
pre-existing untracked paths.

## 7. Commit / push record

The commit and push were performed as the final act of this closure (after
all verification in §1–§5 passed). See the phase summary output for the
exact commit hash, push result, and post-push working-tree state; the
commit contains exactly the files listed in §6 and nothing else.
