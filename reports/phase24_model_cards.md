# Phase 24 — Candidate Model Cards (Verified)

**Verification method:** official Hugging Face Hub API (`huggingface.co/api/models/...`) metadata — tags, `base_model`, `sha`, license tags, architecture, chat template — fetched live on **2026-09-17**. No blog sources. Fields that could not be verified are marked **UNVERIFIED** rather than guessed.

**Headline finding:** the current control `Qwen/Qwen2.5-1.5B-Instruct` HEAD is `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` — byte-identical to the revision pinned in `training/trl_sft_job.py` / `trl_sft_job_v02.py`. The pinned base is the current upstream HEAD.

---

## A. Retained candidates

### A1. Qwen/Qwen2.5-1.5B-Instruct — CONTROL (current Platrixa base)
| Field | Verified value |
|---|---|
| Repo ID | `Qwen/Qwen2.5-1.5B-Instruct` |
| Current HEAD SHA | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` (**= project pin**) |
| Type | Causal LM, `Qwen2ForCausalLM` (`model_type: qwen2`) |
| Base model | `Qwen/Qwen2.5-1.5B` (finetune tag) |
| Parameters | ~1.5B |
| Context | 32,768 (per official card family spec) |
| Instruction-tuned | Yes (chat template: Qwen `<\|im_start\|>` ChatML style) |
| Finance training | None claimed (general instruct) |
| License | `apache-2.0` (tag verified) |
| Commercial use | Permitted |
| Quantization | Widely available (GPTQ/AWQ/GGUF ecosystem) |
| T4 feasibility | bf16 ≈ 3–4 GB; comfortable |
| Source | https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct |
| Checked | 2026-09-17 |

### A2. WiroAI/WiroAI-Finance-Qwen-1.5B — domain-specialization comparison (same size class)
| Field | Verified value |
|---|---|
| Repo ID | `WiroAI/WiroAI-Finance-Qwen-1.5B` |
| HEAD SHA | `f79cfbc65f90691b879f1a3960bf02a89f998ae6` |
| Type | Causal LM, `Qwen2ForCausalLM` |
| **Base model** | **`Qwen/Qwen2.5-Math-1.5B`** — NOT `Qwen2.5-1.5B-Instruct`. The "same-base" comparison is therefore **same-size, not same-base**; must be documented in all comparisons |
| Training data disclosed | `Josephgflowers/Finance-Instruct-500k` (dataset tag on card) |
| Instruction-tuned | Yes — **DeepSeek-style chat template** (`<\|User\|>` / `<\|Assistant\|>`), NOT Qwen ChatML. The scout runner must apply the model's own template |
| License | `apache-2.0` |
| Downloads | 32 (very low adoption; community-unvetted) |
| Context | UNVERIFIED (card metadata did not expose it) |
| T4 feasibility | bf16 ≈ 3–4 GB |
| Source | https://huggingface.co/WiroAI/WiroAI-Finance-Qwen-1.5B |
| Checked | 2026-09-17 |
| Notes | Finance-specialized at the exact 1.5B size class — the closest available apples-to-apples "does domain specialization help at this size" probe. Math-base provenance and tiny adoption are material caveats |

### A3. Qwen/Qwen2.5-3B-Instruct — scale step
| Field | Verified value |
|---|---|
| Repo ID | `Qwen/Qwen2.5-3B-Instruct` |
| Type | Causal LM, `Qwen2ForCausalLM` |
| Base | `Qwen/Qwen2.5-3B` |
| Instruction-tuned | Yes (Qwen ChatML) |
| **License** | **`license:other` — Qwen research license; NON-commercial** (verified tag) |
| Commercial use | **Restricted** — research use only |
| T4 feasibility | bf16 ≈ 7–8 GB; tight but feasible |
| Source | https://huggingface.co/Qwen/Qwen2.5-3B-Instruct |
| Checked | 2026-09-17 |
| Notes | Usable as a diagnostic scale probe only; NOT shippable under current licensing |

### A4. Qwen/Qwen2.5-7B-Instruct — upper scale bound
| Field | Verified value |
|---|---|
| Repo ID | `Qwen/Qwen2.5-7B-Instruct` |
| Type | Causal LM, `Qwen2ForCausalLM` |
| Base | `Qwen/Qwen2.5-7B` |
| Instruction-tuned | Yes (Qwen ChatML) |
| **License** | **`license:other` — Qwen research license; NON-commercial** |
| T4 feasibility | bf16 ≈ 15–16 GB — **exceeds T4 16 GB with KV cache**; requires 4-bit quant (~5–6 GB) or an A10/24 GB-class host |
| Source | https://huggingface.co/Qwen/Qwen2.5-7B-Instruct |
| Checked | 2026-09-17 |
| Notes | Capability ceiling probe; likely NOT_RUN—HARDWARE on stock T4 in bf16 |

### A5. WiroAI/WiroAI-Finance-Qwen-7B — finance-specialized at 7B
| Field | Verified value |
|---|---|
| Repo ID | `WiroAI/WiroAI-Finance-Qwen-7B` |
| Type | Causal LM, `Qwen2ForCausalLM` |
| **Base model** | **`Qwen/Qwen2.5-Math-7B`** (verified tag) |
| Training data | UNVERIFIED beyond repo tags |
| License | `apache-2.0` |
| Instruction-tuned | Yes (same WiroAI DeepSeek-style template family) |
| T4 feasibility | bf16 exceeds T4; 4-bit quant required |
| Source | https://huggingface.co/WiroAI/WiroAI-Finance-Qwen-7B |
| Checked | 2026-09-17 |

### A6. OVHaiLLM/Qwen-Open-Finance-R-8B (requested as `DragonLLM/Qwen-Open-Finance-R-8B`)
| Field | Verified value |
|---|---|
| Repo ID | `OVHaiLLM/Qwen-Open-Finance-R-8B` — the `DragonLLM/...` ID **redirects** to this repo (verified via API redirect) |
| Type | Causal LM |
| **Base model** | **Qwen3-8B-Base lineage** (verified tag) — note: Qwen3 architecture, not Qwen2.5 |
| Pipeline tag | Question-answering (card-oriented toward finance QA; not a small-IR extractor) |
| **Access** | **`gated: auto`** — requires an HF account accepting terms; complicates headless GPU-host pulls |
| License | `apache-2.0` (tag) |
| Training data | UNVERIFIED beyond card claims |
| T4 feasibility | bf16 exceeds T4; 4-bit quant required |
| Source | https://huggingface.co/OVHaiLLM/Qwen-Open-Finance-R-8B |
| Checked | 2026-09-17 |
| Notes | Retained as the finance-specialized reasoning comparison; gated access + QA orientation are material deployment caveats |

### A7. DeepSeek-R1-Distill family — reasoning comparison
| Field | Value |
|---|---|
| Requested ID | `deepseek-ai/DeepSeek-R1-Distill-Qwen-8B` |
| Status | **UNVERIFIED — HF API returned 401 for this ID twice from this environment.** Not marked available; runner must resolve + hash at download time and FAIL CLOSED if unresolvable |
| Verified sibling | `deepseek-ai/DeepSeek-R1-Distill-Qwen-7B` — HEAD `916b56a44061fd5cd7d6a8fb632557ed4f724f60`, `license:mit`, `Qwen2ForCausalLM`, base `Qwen/Qwen2.5-Math-7B`, **`<think>`-reasoning chat template** (verified) |
| Implication | The reasoning family emits `＜think＞…＜/think＞` before answers; the scout runner must strip think-blocks before JSON parsing and budget max_new_tokens accordingly, or reasoning models will exhaust the 512 budget inside the think block |
| Source | https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B |
| Checked | 2026-09-17 |

---

## B. Dropped / excluded candidates

| Candidate | Status | Reason |
|---|---|---|
| `dschauhan08/Qwen2.5-3B-Finance-GGUF` | **DROPPED** | Not verifiable — no resolvable current model card / provenance found |
| `ProsusAI/finbert` | **NOT A FOUNDATION CANDIDATE** | Verified: `BertForSequenceClassification`, sentiment-classification task (financial phrasebank). Not generative; cannot emit CandidateSemanticIR. No benchmark slot |
| Embedding / classification / forecasting / OCR-only models | Excluded by design | Phase 24 requires generative, instruction-following models compatible with the 18-field contract |

---

## C. Runner implications (contract for `fte_fyjc_76`)

1. **Template handling:** Qwen-family (A1/A3/A4) use ChatML; WiroAI (A2/A5) and R1-Distill (A7) use DeepSeek-style templates with distinct BOS/EOS. The runner must use each model's own `chat_template` via the tokenizer — no hand-rolled prompt format.
2. **Reasoning models (A6, A7):** strip `<think>…</think>` before JSON parse; consider max_new_tokens > 512 for these only if recorded as a protocol deviation (historical contract is 512).
3. **Licensing gate:** A3/A4 are research-only — flagged in the report's GO/NO-GO; they are diagnostics, never deployment candidates.
4. **Gated model (A6):** requires user-accepted HF terms on the GPU host; expect an auth error otherwise — record NOT_RUN—ACCESS, not a capability result.
5. **Fail-closed IDs:** any candidate whose repo cannot be resolved is NOT_RUN—ACCESS with the exact error recorded; never a fabricated score.
