# PLATRIXA — PHASE 18 REAL EXECUTION + PERMANENT PERSISTENCE REPORT

**Date:** 2026-09-15
**Phase:** 18 — Real Execution + Permanent Persistence (execution + infrastructure + persistence ONLY)
**Final status:** ⛔ **BLOCKED** (real 92-case execution; memory gate not met) — with the persistence/chain half **PROVEN on real PostgreSQL**
**Evidence suite:** `scripts/fte_fyjc_69_phase18_persistence_test.py` — **49/49 PASS**
**Predecessor:** Phase 17 closed as `d761e73` ("phase 17: harden audit chain — semantic IR boundary, evidence chain, locked 92-case benchmark"), pushed to `origin/main` (verified: `git status -sb` in sync after push).

---

## 1. Phase objective

Prove the already-built architecture under real execution and permanent
persistence: ≥8 GB RAM host → 92 locked cases through the real Qwen+LoRA
model and runtime → real compositional metrics → ExecutionEvidence →
cryptographic chain → Railway PostgreSQL → retrieval + integrity
verification. No features added. No locked structure modified.

## 2. Infrastructure (measured, not asserted — §1)

| Item | Measured value | Source |
|---|---|---|
| RAM total | **2.95 GiB** | `/proc/meminfo` (fte_69 §A) |
| RAM available | 2.6–2.7 GiB | `/proc/meminfo` |
| Swap | **0 B** | `/proc/meminfo`, `swapon` |
| Disk free | 4.0 GB volume at ~100% (30–72 MB free during runs) | `df` / `os.statvfs` |
| CPU | 2 | `os.cpu_count()` |
| Python | 3.10.12 | runtime |
| Memory gate (≥8 GiB + swap) | **NOT MET** | fte_69 §A records it |

The gate is evaluated in code (`section_a()`), not asserted by the
operator — the same numbers are printed into the suite evidence.

## 3. Exact model identity (locked, §2)

From the pinned production boundary `backend/model_provider/base.py`:

| Field | Value |
|---|---|
| Base model | `Qwen/Qwen2.5-1.5B-Instruct` |
| Base revision | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| Adapter repo | `Pranay-20/platrixa-fyjc-specialist-v0.1` |
| Adapter revision | `b5c0a37cebc00e93144150dbbcaa7b28cadb259e` |
| Inference config | `max_new_tokens=512`, `temperature=0.0` (greedy), `top_p=1.0`, `do_sample=False` (ProviderConfig pins) |

Verified unchanged in fte_69 §B (family, repo, full 40-hex revisions).
Both artifacts are present in the local HF cache (Phase 17 evidence).

## 4. Locked 92-case benchmark identity (§3)

- File: `training/phase17_benchmark.jsonl`
- Version: `phase17-benchmark-v1`
- sha256 (exact file bytes): `af1ab919f6fcb196260652b5af925f5addc0f6d5117813d8e0472ddaa0281ba1`
- Cases: **92** (40 core / 24 counterfactual in 12 pairs / 20 adversarial / 8 paraphrase), PB- IDs unique
- Verified this phase: fte_69 §C asserts count, ID uniqueness, version,
  harness-lock == file-bytes sha256, category set. **Nothing about the
  benchmark was modified in Phase 18.**

## 5. Real execution result (§5/§13)

**NOT RUN — BLOCKED.** The measured environment (2.95 GiB RAM, 0 swap)
cannot load the ~3.1 GiB fp16 Qwen weights, exactly as proven in Phase 17
(weights load 338/338 shards, then generation is OOM-killed). Per §1 the
phase STOPs here with the measured evidence; per §13 the benchmark was
NOT faked, sampled, substituted, quantized, or otherwise "made to fit."
fte_69 §K records the skip as `SKIP_BY_INFRASTRUCTURE` with the measured
numbers — an honest skip, never a fabricated result.

## 6. Full compositional metrics (§5)

**None exist, and none are invented.** Metrics are defined only by
executions of the locked cases through the real model; there were zero
real executions. `training/phase17_benchmark.py` remains the scoring
authority (whole-transaction headline, field metrics diagnostic) for the
future run on a ≥8 GB host. No numerator/denominator tables are faked
here.

## 7. ExecutionEvidence generation (§6)

The Phase 17 structure (`exec-evidence-1`, 13 fields) is **unchanged** —
no field was added, dropped, or renamed to simplify persistence. The
persistence layer stores exactly `ExecutionEvidence.to_dict()`; nothing
parallel was created.

Mechanism validation (explicitly labeled, per §13's narrow-test
allowance): fte_69 §E builds 12 records (`P18-MECH-*`) whose fields come
from ACTUAL runtime identities — the pinned model identity of §3, the
real rule-pack file digest, and the Phase 17 hash definitions — and
pushes them through the full persist→retrieve path on real PostgreSQL.
These records are **mechanism-validation evidence, NOT model-execution
evidence**; they make no claim about the model.

## 8. Cryptographic audit chain (§7)

Proven over real PostgreSQL rows (fte_69 §F–G, §I):

- chain digest definition unchanged: `sha256(prev_digest + canonical_json(payload))`, genesis `0×64` (documented in `backend/semantics/persistence.py`);
- 12/12 rows verify end-to-end; genesis anchors; mid-chain recomputation deterministic;
- **tamper detection**: a forged `final_state` is detected at its exact
  ledger position and **left in place** (recorded, never repaired —
  §7 discipline; an append-only chain cannot be surgically rewritten);
- **durable tamper detection**: a fresh interpreter detects the SAME
  tamper at the SAME position;
- duplicate `request_id` INSERT rejected (immutability);
- the Phase 17 in-kernel chain (`fte_67` 48/48) is untouched.

## 9–10. PostgreSQL persistence + retrieval (§8–§10)

- Smallest change, existing boundary: new table `platrixa_execution_evidence`
  registered on the SAME declarative Base as every other Platrixa model
  (`backend/database/db.py`); DDL file mirrors the Phase 16 schema-script
  precedent; idempotent init module `python -m backend.semantics.init_evidence_store`.
  No second persistence subsystem. INSERT-only (no UPDATE/delete paths in
  the store).
- Write proof: 12/12 persisted on a **real** embedded PostgreSQL server
  (`pgserver` — not SQLite, not a mock), 12/12 retrievable
  **field-identical** (13 evidence fields compared per record), 12/12
  chain-valid. Persistence cost: ~0.04 s for 12 rows.
- **Railway-specific: NOT PROVEN.** The sandbox has no reachable Railway
  `DATABASE_URL` (the ambient URL was already proven unreachable during
  Phase 16; no secret values were read). Deployment path for the real
  store: set `DATABASE_URL` on Railway, run
  `python -m backend.semantics.init_evidence_store` once, then every
  persistence call targets Railway. §9's "Railway PostgreSQL rows" and
  §10's Railway retrieval therefore remain **NOT PROVEN** until executed
  on the connected host.

## 11. Restart / durability (§11)

**Engine-level durability: PROVEN** — a brand-new interpreter process
(with only the connection string, no shared memory) re-read 12/12 rows
and re-detected the recorded tamper at the same position (fte_69 §I).
**Railway restart durability: NOT PROVEN** (requires the managed host;
explicitly marked per §11 rather than inferred from INSERT success).

## 12. Fail-closed behavior (§12)

- unreachable PostgreSQL → store raises on read AND write (never silent success) — fte_69 §H;
- store module imports cleanly with no `DATABASE_URL` (lazy engine; Phase 16 import-safety lesson applied);
- missing evidence fields → rejected at the boundary, never invented;
- Kernel/RuleEngine authority untouched: GroundedSemanticIR-only
  accounting still enforced, raw candidates still rejected, RuleEngine
  still has no VERIFIED authority (fte_69 §J ↔ Phase 17 contracts);
- Phase 17 suites still green (no gate weakened anywhere).

## 13. No-mocks ledger (§13)

Real embedded PostgreSQL used for every persistence/chain/durability
proof; no fake DB pretending to be Railway is presented as the Railway
result. The only stubbed object is the Kernel's model provider inside
narrow unit checks (J) — explicitly NOT offered as Phase 18 execution
evidence. Zero fabricated benchmark outputs exist anywhere in this phase.

## 14. Tests (§14) — results

| Suite | Result | Covers |
|---|---|---|
| `fte_fyjc_69_phase18_persistence_test.py` | **49/49 PASS** | A memory gate, B model lock, C benchmark lock, D real-PG DDL (idempotent), E 12/12 persist+retrieve field-identical, F chain integrity, G tamper+duplicate, H fail-closed store, I cross-process durability, J authority (§14 L/M), K honest infrastructure skip |
| `fte_fyjc_67` | 48/48 PASS | Phase 17 boundaries/regressions (§14 N) |
| `fte_fyjc_68` | 51/51 PASS | rules + benchmark lock (§14 N) |
| `fte_fyjc_52, 53, 59, 60, 61, 62` | all PASS | Phase 7C–13 regressions |
| `fte_fyjc_65` | 47/47 PASS | Phase 15 hosted-API security |
| `fte_fyjc_66` | 51/51 PASS | Phase 16 metered gate (real PostgreSQL) |

(fte_66 required one retry: its first run hit a disk-full `initdb` failure
after its pgdata artifact had been reclaimed — environmental, not a
regression; green on retry.)

## 15. Performance observations (§15)

- persistence: 12 rows in ~0.04 s (~3 ms/row, chain digest included);
- model execution: N/A (blocked); no latency numbers invented;
- memory: no OOM observed outside the known model-load blocker; pgserver cluster ~38 MB on disk (reclaimed after runs).

## 16. Files changed (Phase 18 — all uncommitted, per §17)

```
backend/semantics/persistence.py                      NEW  store: INSERT-only, chain digest, fail-closed, lazy imports
backend/semantics/init_evidence_store.py              NEW  idempotent DDL init (Phase 16 convention)
backend/database/evidence_model.py                    NEW  ORM row on the EXISTING Base
backend/database/platrixa_execution_evidence_schema.sql NEW  DDL (mirrors model)
scripts/fte_fyjc_69_phase18_persistence_test.py       NEW  49-check evidence suite
PLATRIXA_PHASE18_REAL_EXECUTION_REPORT.md             NEW  this report
```

**Files intentionally untouched:** the locked benchmark (`training/phase17_benchmark.jsonl`), its harness, `backend/kernel/kernel.py`, `backend/semantics/{ir,evidence}.py`, `rules.md`, all Phase 10–16 boundaries, `hf_space/`, `training_data/`, `content_bank/`, `.env*`.

## 17. Remaining blockers

1. **≥8 GB RAM + swap host** for the real 92-case execution (then
   `python3 training/phase17_benchmark.py --adapter production` fills the
   metrics sections of a successor report).
2. **Reachable Railway `DATABASE_URL`** (user-provisioned on the host)
   to run `init_evidence_store` and convert the proven persistence
   mechanism into the Railway-specific 92/92 write/retrieve/restart proof.

## 18. Final status: **BLOCKED**

Per §1's own decision rule ("if the environment still cannot provide
enough memory … STOP and report BLOCKED with the actual infrastructure
evidence"). Honest sub-ledger:

| Claim | Status |
|---|---|
| Infrastructure ≥8 GB + swap | ❌ NOT MET (2.95 GiB / 0 swap — measured) |
| 92/92 genuine executions | ❌ not run (blocked) |
| Metrics from real executions | ❌ none exist |
| ExecutionEvidence structure preserved | ✅ proven (unchanged, fte_67) |
| Crypto chain over persisted rows | ✅ proven (12/12 + tamper + duplicates, real PostgreSQL) |
| Persistence mechanism (real PostgreSQL) | ✅ proven (12/12 write + retrieve field-identical) |
| Railway-specific persistence + restart | ❌ NOT PROVEN (no reachable Railway URL in sandbox) |
| Fail-closed behavior preserved | ✅ proven (store + Kernel + RuleEngine) |
| Regressions green | ✅ 67/68/52/53/59/60/61/62/65/66 |
