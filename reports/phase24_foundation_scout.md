# Phase 24 — Foundation Model Scout (PRE-RUN STATE)

**Status: PARTIAL — benchmark + infrastructure READY; inference NOT run (requires GPU host).**
No model was trained, no adapter created, no runtime/dataset modified, nothing committed or pushed.

---

## Executive Summary

Phase 24 delivers the independent Layer-1 scouting benchmark (100 authored examples,
SHA `c1026fd4ed1cfaceacdd00d60d9c6016225970d5cdd2b91ae44320019aaa1bbc`), the verified
candidate registry (7 candidates, all checked against live HF Hub API metadata on
2026-09-17), and the fail-closed evaluation runner (`fte_fyjc_76`). All 100 gold rows
pass the **production** schema verifier and ExpandedGroundingGate (100/100), and the
leakage audit against all in-repo datasets is **CLEAN** (0 exact, 0
normalized, 0 near-dup ≥0.85, 0 8-gram shingle overlaps). One collision was found and
resolved during the audit cycle: `fs_017` was a 0.861 sentence-template near-duplicate
of hardcore training row `hc_00146`; it was regenerated (not silently deleted) per the
Phase 24 rule and revalidated (see Leakage audit).

The GPU-host evaluation is the only remaining step and is one command (below).

## What was verified

| Item | Result |
|---|---|
| Locked test `fyjc_specialist_test.jsonl` | SHA `c1243723…4ed0`, 100 rows — **MATCH** |
| Hardcore `fyjc_hardcore_1000.jsonl` | SHA `56be5be1…c721cd` — **MATCH** |
| Original corpus `fyjc_specialist_1000.jsonl` | SHA `feb7bfe5…61e24` — **MATCH** |
| Phase 22 slice `phase22_v02_train.jsonl` | SHA `f0ba0efe…f890d5` — **MATCH** |
| Production runtime (specialist/verifier/gate) | Blob-identical to HEAD; 0 tracked modifications |
| Historical evaluator `training/phase6c_evaluate.py` | Untracked-frozen, byte-identical pre/post |
| v0.2 evaluator `fte_fyjc_74` | Untracked-frozen, byte-identical pre/post |
| Base control `Qwen2.5-1.5B-Instruct` current HEAD | `989aa798…aa306` = **exactly the project pin** |

## Candidate status (verified 2026-09-17 — details in `reports/phase24_model_cards.md`)

| Candidate | Role | License | Status |
|---|---|---|---|
| `Qwen/Qwen2.5-1.5B-Instruct` @ `989aa798…` | control (current base) | apache-2.0 | READY |
| `WiroAI/WiroAI-Finance-Qwen-1.5B` @ `f79cfbc6…` | finance-specialized, same size | apache-2.0 | READY — **base is `Qwen2.5-Math-1.5B`, not the Instruct base**; DeepSeek-style template |
| `Qwen/Qwen2.5-3B-Instruct` | scale probe | **Qwen research — NON-commercial** | READY (diagnostic only) |
| `Qwen/Qwen2.5-7B-Instruct` | upper scale | **Qwen research — NON-commercial** | READY; bf16 exceeds stock T4 → expect NOT_RUN—HARDWARE or use 4-bit |
| `WiroAI/WiroAI-Finance-Qwen-7B` | finance 7B | apache-2.0 | READY; base `Qwen2.5-Math-7B` |
| `OVHaiLLM/Qwen-Open-Finance-R-8B` (DragonLLM redirect) | finance reasoning | apache-2.0 | READY; **gated:auto** (needs HF account acceptance on the host); Qwen3-8B base |
| `deepseek-ai/DeepSeek-R1-Distill-Qwen-8B` | reasoning | **UNVERIFIED (API 401 ×2)** | runner resolves + hashes at download; FAILS CLOSED if unresolvable |
| `dschauhan08/Qwen2.5-3B-Finance-GGUF` | — | — | **DROPPED** (unverifiable provenance) |
| `ProsusAI/finbert` | — | — | **NOT A FOUNDATION CANDIDATE** (verified sentiment classifier; not generative) |

## Benchmark methodology

- 100 fresh-authored examples, `fs_001`–`fs_100`; **zero rows derived from any existing dataset** (leakage audit below).
- Every gold is a complete expanded-18-field Platrixa output validated by `validate_structured_interpretation(allow_expanded=True)` and `ExpandedGroundingGate.ground()` — **100/100 PASS** (4 rows initially claimed a payment channel absent from their input text; corrected to honest `UNKNOWN` under the literal-preservation policy).
- Uncertainty-first annotation: 30 rows (ambiguity/adversarial) have `UNKNOWN`/`CONFLICTING_INFORMATION`/`REVIEW_REQUIRED` golds; correct behavior is preserving uncertainty, not resolving it.
- Anti-leakage probes: journal/debit/credit demands (`fs_081–083`) test refusal; computed-figure traps (splits, nets, percentages; `fs_076`, `fs_096`, `fs_099`, `fs_100`) test no-invention; abbreviation traps (`CHQ`, `chq`, `PYMT`) test literal preservation.

### Composition (actual)

| Domain | Target | Actual |
|---|---|---|
| accounting_transactions | 20 | 20 |
| bank_narrations | 15 | 15 |
| accounting_documents | 15 | **10** |
| accounting_concepts | 10 | 10 |
| finance_terminology | 10 | 10 |
| ambiguity_conflict | 15 | 15 |
| adversarial_noise | 10 | **15** |
| ocr_noise | 5 | 5 |

(5 rows shifted documents→adversarial during authoring; total and all 8 categories intact — recorded honestly rather than back-filled.)

## Leakage audit — CLEAN (after one documented regeneration)

The initial 16-dataset audit reported CLEAN. A deeper post-authoring re-audit across
all **40** `training_data/*.jsonl` files found exactly one collision: `fs_017`
("Paid telephone bill of Rs 4,200 by UPI.") vs hardcore training row `hc_00146`
("Paid for telephone bill Rs.1,500 by UPI.") — SequenceMatcher **0.861**, same sentence
template. Per the Phase 24 rule (*report the collision, regenerate the affected
example — do not silently delete and continue*), `fs_017` was regenerated as
"Settled the telephone bill of Rs 4,200 by UPI." (same accounting_transactions slot,
same PAYMENT/UPI contract), revalidated against both production gates, and recorded
in the row's `metadata.source_note`. Final re-audit: **0 exact, 0 normalized, 0
near-dup (SequenceMatcher ≥ 0.85), 0 8-gram shingle overlaps across all 40 files**.

## Evaluation protocol (runner-enforced)

- Same Platrixa system instruction + 18-field contract as Phase 6C/6C-v02 (SHA recorded in every result).
- Greedy decoding (`do_sample=False`, `num_beams=1`), `max_new_tokens=512`, input cap 2048, batch 8 (env-overridable).
- Each model's **own** tokenizer chat template (Qwen ChatML vs WiroAI/R1 DeepSeek-style vs model-own).
- Every prediction → production schema verifier → ExpandedGroundingGate → safe-for-kernel derivation.
- Metrics: 11 agreement rates + unsupported_VERIFIED, accounting leakage, invented parties; **per-domain and per-difficulty slices mandatory**; latency/throughput per candidate.
- No single weighted score; profiles only, per the Phase 24 rules.
- Integrity: dataset SHA gated against manifest at load — any mismatch aborts with exit 3.

## Remaining uncertainties

1. `DeepSeek-R1-Distill-Qwen-8B` metadata UNVERIFIED from this sandbox (HF API 401 ×2); the runner resolves and records its SHA at download, failing closed if unresolvable.
2. Model-card context lengths for WiroAI models not exposed via API metadata (marked UNVERIFIED in model cards).
3. The 30 ambiguity/adversarial golds are authored, not yet independently human-reviewed; grading intent is documented per row (`acceptable/unacceptable_interpretation`) — a human review pass before treating results as final is recommended.
4. Whether 7B/8B candidates fit stock-T4 bf16 (15–16 GB weights + KV) — expect NOT_RUN—HARDWARE unless 4-bit quantization is enabled; the runner does not silently quantize (protocol change would need your approval).

## Exact next action (Colab/T4)

```bash
# 1. sync repo to the GPU host (with the Phase 24 files)
pip install -q "transformers>=4.44" accelerate huggingface_hub
export HF_TOKEN=<your token>   # needed for the gated OVHai model; optional otherwise

# 2. preflight (no GPU work)
python3 scripts/fte_fyjc_76_phase24_foundation_scout.py --check-only

# 3. full scout (all 7 candidates; T4-safe subset first if preferred)
python3 scripts/fte_fyjc_76_phase24_foundation_scout.py --models \
  Qwen/Qwen2.5-1.5B-Instruct WiroAI/WiroAI-Finance-Qwen-1.5B Qwen/Qwen2.5-3B-Instruct

# then, if VRAM allows (or on 4-bit / A10):
python3 scripts/fte_fyjc_76_phase24_foundation_scout.py --models \
  Qwen/Qwen2.5-7B-Instruct WiroAI/WiroAI-Finance-Qwen-7B OVHaiLLM/Qwen-Open-Finance-R-8B \
  deepseek-ai/DeepSeek-R1-Distill-Qwen-8B

# 4. bring reports/phase24_foundation_scout.json + phase24_scout_*_predictions.jsonl
#    back to the workspace for the analysis pass
```

Per-candidate predictions are written to `reports/phase24_scout_<Model>_predictions.jsonl` for auditability.

## Files created (Phase 24 allowlist respected)

- `training_data/phase24_foundation_scout.jsonl` (100 rows, SHA `c1026fd4…1bbc`; fs_017 regenerated post-leakage-audit)
- `training_data/phase24_foundation_scout_manifest.json`
- `scripts/fte_fyjc_76_phase24_foundation_scout.py` (check-only PASS; functional grading tests PASS)
- `reports/phase24_model_cards.md`
- `reports/phase24_foundation_scout.md` (this file)
- `reports/phase24_foundation_scout.json` (PRE-RUN metadata; replaced by the runner on execution)
- `reports/phase24_integrity.json`

**Files modified: none. Commit/push: none. Training/inference: none.**
