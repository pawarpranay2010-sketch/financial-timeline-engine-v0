# P5a — Specialist Model Evaluation Report

## 1. Model Artifact

| Field | Value |
|-------|-------|
| Base model | Qwen/Qwen2.5-1.5B-Instruct |
| Adapter | None |
| Adapter loaded | False |
| Evaluation adapter | deterministic-fallback |
| Note | No trained LoRA artifact found on disk. Using deterministic fallback baseline. |

## 2. Dataset Counts

| Tier | Records |
|------|--------:|
| ambiguity | 20 |
| unsupported | 24 |
| robustness | 9 |
| **Total** | **53** |

## 3. Overall Results

| Metric | Value |
|--------|-------|
| Total evaluated | 53 |
| Valid JSON outputs | 53 |
| Parse failures | 0 |
| Complete interpretation matches | 2 |
| Field-level accuracy | 193/318 (60.7%) |
| Grounding correctness | 5/53 (9.4%) |
| Ambiguity handling | 18/53 (34.0%) |
| Reference resolution | 48/53 (90.6%) |

## 4. Per-Tier Results

### Ambiguity (20 records)

| Metric | Value |
|--------|-------|
| Parse success | 20/20 |
| Complete matches | 2/20 (10.0%) |
| Field accuracy | 79/120 (65.8%) |
| Grounding correct | 4/20 (20.0%) |
| Ambiguity correct | 11/20 (55.0%) |

**Field-level breakdown:**

| Field | Correct | Total | Accuracy | Top Failure |
|-------|--------:|------:|:--------:|-------------|
| transaction_type | 11 | 20 | 55% | mismatch (9) |
| parties | 16 | 20 | 80% | mismatch (4) |
| amounts | 18 | 20 | 90% | mismatch (2) |
| payment_method | 5 | 20 | 25% | mismatch (15) |
| references | 18 | 20 | 90% | mismatch (2) |
| ambiguities | 11 | 20 | 55% | mismatch (9) |

### Unsupported (24 records)

| Metric | Value |
|--------|-------|
| Parse success | 24/24 |
| Complete matches | 0/24 (0.0%) |
| Field accuracy | 79/144 (54.9%) |
| Grounding correct | 1/24 (4.2%) |
| Ambiguity correct | 7/24 (29.2%) |

**Field-level breakdown:**

| Field | Correct | Total | Accuracy | Top Failure |
|-------|--------:|------:|:--------:|-------------|
| transaction_type | 5 | 24 | 21% | mismatch (19) |
| parties | 21 | 24 | 88% | missing (2) |
| amounts | 24 | 24 | 100% | — (0) |
| payment_method | 1 | 24 | 4% | mismatch (23) |
| references | 21 | 24 | 88% | mismatch (3) |
| ambiguities | 7 | 24 | 29% | mismatch (17) |

### Robustness (9 records)

| Metric | Value |
|--------|-------|
| Parse success | 9/9 |
| Complete matches | 0/9 (0.0%) |
| Field accuracy | 35/54 (64.8%) |
| Grounding correct | 0/9 (0.0%) |
| Ambiguity correct | 0/9 (0.0%) |

**Field-level breakdown:**

| Field | Correct | Total | Accuracy | Top Failure |
|-------|--------:|------:|:--------:|-------------|
| transaction_type | 8 | 9 | 89% | mismatch (1) |
| parties | 8 | 9 | 89% | fabricated (1) |
| amounts | 9 | 9 | 100% | — (0) |
| payment_method | 1 | 9 | 11% | mismatch (8) |
| references | 9 | 9 | 100% | — (0) |
| ambiguities | 0 | 9 | 0% | mismatch (9) |

## 5. Failure Analysis

| Failure Category | Count |
|-----------------|------:|
| missed_ambiguity | 27 |
| fabricated_parties | 5 |

### Failed Records

| Problem ID | Tier | Input (truncated) | Failed Fields |
|-----------|------|-------------------|---------------|
| C0076 | ambiguity | Balances as on 1st April: Cash Rs.50000, Bank Rs.1... | transaction_type, ambiguities |
| C0040 | ambiguity | Purchased goods from Raj for Rs.20000. Paid Rs.196... | transaction_type, payment_method, ambiguities |
| C0054 | ambiguity | Started business with cash Rs.200000. Purchased fu... | amounts |
| C0022 | ambiguity | Received Rs.20000 from Raj in full settlement of h... | transaction_type, parties, payment_method, references, ambiguities |
| C0098 | ambiguity | M.pictureBox se Rs.20000 ka samaan khareeda. | transaction_type, payment_method, ambiguities |
| C0028 | ambiguity | Purchased goods from Raj for Rs.40000. Sold half o... | payment_method |
| C0058 | ambiguity | Raj sent us goods worth Rs.20000. | transaction_type, payment_method, ambiguities |
| C0046 | ambiguity | Purchased goods for Rs.9999 plus 18% GST. | payment_method |
| C0050 | ambiguity | Returned 5 kgs of damaged goods to Raj purchased a... | transaction_type, parties, payment_method |
| C0056 | ambiguity | Purchased and sold goods from Raj for Rs.20000. | payment_method |
| C0023 | ambiguity | Raj settled his account of Rs.25000 by paying Rs.2... | transaction_type, payment_method, references, ambiguities |
| C0091 | ambiguity | Purchased goods from Rajesh Kumar Sharma & Sons Pr... | parties, payment_method |
| C0042 | ambiguity | Purchased 100 kgs of rice from Raj at Rs.50 per kg... | parties, payment_method |
| C0018 | ambiguity | Purchased goods from Raj for Rs.30000. Paid one-th... | payment_method, ambiguities |
| C0080 | ambiguity | Started business with cash Rs.200000. Purchased go... | amounts |
| C0072 | ambiguity | Placed an order with Raj for goods worth Rs.20000. | transaction_type, payment_method, ambiguities |
| C0049 | ambiguity | Goods purchased from Raj for Rs.10000. Returned de... | transaction_type, payment_method |
| C0051 | ambiguity | Purchased goods from Raj for Rs.20000. Paid carria... | payment_method, ambiguities |
| C0029 | unsupported | Purchased goods from Raj for Rs.30000. Sold some g... | transaction_type, payment_method, references |
| C0074 | unsupported | Agreed to purchase goods from Raj for Rs.20000. | transaction_type, parties, payment_method |
| C0024 | unsupported | Raj settled his account with us. | transaction_type, payment_method, references, ambiguities |
| C0069 | unsupported | Rs.20000 | transaction_type, payment_method, ambiguities |
| C0027 | unsupported | Purchased goods from Raj for Rs.50000. Paid Rs.200... | transaction_type, payment_method, references, ambiguities |
| C0099 | unsupported | राज यांकडून रु. २०००० चा माल खरेदी केला. | transaction_type, payment_method, ambiguities |
| C0062 | unsupported | Goods were purchased from Raj for Rs.20000. | payment_method |
| C0060 | unsupported | Raj se Rs.20000 ka samaan khareeda. | transaction_type, payment_method, ambiguities |
| C0078 | unsupported | xyz123 abc | transaction_type, payment_method, ambiguities |
| C0073 | unsupported | We will purchase goods from Raj for Rs.20000. | transaction_type, payment_method |
| C0065 | unsupported | Purchase: Raj | Amount: Rs.20000 | Payment: Cash | transaction_type |
| C0063 | unsupported | What is the journal entry for purchasing goods fro... | transaction_type, payment_method, ambiguities |
| C0075 | unsupported | Raj submitted a quotation for Rs.20000. | transaction_type, payment_method, ambiguities |
| C0034 | unsupported | Received goods worth Rs.15000 from Raj. It was ret... | transaction_type, payment_method, ambiguities |
| C0070 | unsupported | Purchased | payment_method, ambiguities |
| C0011 | unsupported | Acquired computer equipment from Dell for Rs.80000 | transaction_type, payment_method, ambiguities |
| C0067 | unsupported | Particulars: Purchases from Raj, Amount: 20000, Dr... | transaction_type, payment_method, ambiguities |
| C0059 | unsupported | We have purchased from M/s Raj & Co. goods valued ... | payment_method |
| C0089 | unsupported | ... | payment_method, ambiguities |
| C0012 | unsupported | Obtained printing services from PrintHub for Rs.12... | transaction_type, parties, payment_method, ambiguities |
| C0066 | unsupported | On Monday morning, the business purchased raw mate... | parties, payment_method |
| C0068 | unsupported | Raj | transaction_type, payment_method, ambiguities |
| C0077 | unsupported | The weather is nice today. | transaction_type, payment_method, ambiguities |
| C0010 | unsupported | Procured raw materials from Ganesh Traders worth R... | transaction_type, payment_method, ambiguities |
| C0014 | robustness | Purchased goods from Raj for some amount | payment_method, ambiguities |
| C0015 | robustness | Purchased goods from Raj for twenty thousand rupee... | payment_method, ambiguities |
| C0030 | robustness | Bought goods from Raj for Rs.60000. Sold one-third... | payment_method, ambiguities |
| C0016 | robustness | Sold goods to Amit on credit | ambiguities |
| C0061 | robustness | Ptd gds frm Raj Rs.20k | transaction_type, payment_method, ambiguities |
| C0088 | robustness |     | payment_method, ambiguities |
| C0096 | robustness | Purchased goods from Raj for twenty thousand | payment_method, ambiguities |
| C0087 | robustness |  | payment_method, ambiguities |
| C0013 | robustness | Purchased goods from Raj | parties, payment_method, ambiguities |

## 6. Relative-Amount Test

**No relative-amount failures detected** in the evaluated records.

> Note: the ambiguity and unsupported tiers contain few relative-amount examples.

## 7. Kernel vs AI Separation

| Source | Correct | Total | Accuracy |
|--------|--------:|------:|:--------:|
| AI interpretation | 193 | 318 | 60.7% |
| Kernel accounting truth | (not counted as AI evidence) | — | — |

> The Kernel's deterministic result is NOT counted as evidence that the AI interpretation was correct.
> The Kernel remains the sole authority for accounting truth.

## 8. Decision

**INCONCLUSIVE** — more evaluation/training data required

Criteria: field accuracy 60.7% (threshold: 80%), grounding 5/53 (threshold: 70%)

---
*Generated by P5a evaluation harness. EVAL_MODE=True. No production state was modified.*