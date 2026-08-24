# Sprint 26 — REVIEW_REQUIRED Residual Audit

**Classification: NO TRUSTED API NEEDED YET**

---

## A. Baseline

| Gate | Result |
|------|:------:|
| Sprint 25 Tests | 15/15 PASS ✅ |
| Sprint 16 Problem Engine | 44/44 PASS ✅ |
| Sprint 17 Workflow | 38/38 PASS ✅ |
| Sprint 18 Whole-Problem | 89/89 PASS ✅ |
| Sprint 19 Capability | RELEASE READY ✅ |
| Sprint 24 GST Regression | 5/5 PASS ✅ |
| Settlement Regression | 17/17 PASS ✅ |
| Boundary Closure | 852/852 PASS ✅ |
| Chaos Audit | 0 failures ✅ |
| Safety Invariants | All zero ✅ |
| py_compile | PASS ✅ |
| git diff --check | PASS ✅ |

---

## B. Residual Corpus

| Metric | Count |
|--------|:-----:|
| Total test cases | 20 |
| VERIFIED | 8 |
| REVIEW_REQUIRED | 9 |
| INVALID_INPUT_MATH | 1 |
| NOT_SUPPORTED | 0 |

---

## C. Category Breakdown

| Category | Count | Has Confidence Gate | Student-Resolvable |
|----------|:-----:|:-------------------:|:------------------:|
| **A — Missing student intent** | 4 | ✅ CASH_CREDIT | ✅ Yes |
| **B — Historical ambiguity** | 1 | ❌ No | ⚠️ Partially |
| **C — Splitter limitation** | 0 | N/A | N/A |
| **D — Multi-amount** | 0 | N/A | N/A |
| **E — GST without scheme** | 2 | ✅ GST_SCHEME | ✅ Yes |
| **F — Genuine ambiguity** | 2 | ❌ No | ❌ No |
| **G — Incorrect REVIEW_REQUIRED** | 0 | — | — |

**Key finding: 0 Incorrect REVIEW_REQUIRED cases.** Every remaining REVIEW_REQUIRED is correct.

---

## D. Sprint 25 Impact

| Metric | Pre-Sprint 25 | Post-Sprint 25 | Delta |
|--------|:------------:|:--------------:|:-----:|
| Total REVIEW_REQUIRED | 9 | 9 | 0 |
| With Confidence Gate | 2 (GST only) | 6 | +4 |
| Without Confidence Gate | 7 | 3 | -4 |
| Student-resolvable rate | 22% | 67% | +45pp |
| Incorrect REVIEW_REQUIRED | 0 | 0 | 0 |

### What Sprint 25 Actually Changed

**Before Sprint 25:**
- "Purchased goods for 10000." → REVIEW_REQUIRED, no gate, student stuck
- "Sold goods for 20000." → REVIEW_REQUIRED, no gate, student stuck
- "Purchased stock for Rs.50000." → REVIEW_REQUIRED, no gate, student stuck
- "Purchased goods for Rs.5000." → REVIEW_REQUIRED, no gate, student stuck
- "Purchased goods for Rs.10000, GST @ 18%, paid cash." → REVIEW_REQUIRED, GST_SCHEME gate ✅
- "Sold goods to Mohan for Rs.20000, GST @ 12%." → REVIEW_REQUIRED, GST_SCHEME gate ✅

**After Sprint 25:**
- "Purchased goods for 10000." → REVIEW_REQUIRED, **CASH_CREDIT gate** → student resolves ✅
- "Sold goods for 20000." → REVIEW_REQUIRED, **CASH_CREDIT gate** → student resolves ✅
- "Purchased stock for Rs.50000." → REVIEW_REQUIRED, **CASH_CREDIT gate** → student resolves ✅
- "Purchased goods for Rs.5000." → REVIEW_REQUIRED, **CASH_CREDIT gate** → student resolves ✅
- GST cases: unchanged (GST_SCHEME gate pre-existed)

---

## E. Trusted Evidence Analysis

### Cases where trusted evidence would help: **1**

| Case | Text | Why Evidence Helps | Lookup Key | Expected Result |
|------|------|-------------------|------------|-----------------|
| B.01 | "Purchased goods from Mark Rs.90000. Sold half of goods purchased from Mark for cash." | Multiple Mark purchases → fraction resolution ambiguous | `"half of goods purchased from Mark"` + historical index | resolve against Mark's ₹90,000 purchase |

**However**, this case is already correctly handled by `process_problem()` via the historical index — the problem engine's `_resolve_historical_text()` resolves it deterministically when the full problem context is available. The REVIEW_REQUIRED only appears when `orchestrate()` is called on a single isolated transaction without historical context.

### Cases where trusted evidence would NOT help: **8**

| Category | Reason |
|----------|--------|
| Missing student intent (4) | Requires student's payment mode choice — no external data can determine this |
| GST without scheme (2) | Requires student's intra/inter-state choice — already has GST_SCHEME gate |
| Genuine ambiguity (2) | Inclusive GST without rate, or splitter limitation — external data can't resolve |

### Conclusion

The remaining REVIEW_REQUIRED cases fall into two groups:
1. **Student-resolvable (6/9 = 67%)** — Already have Confidence Gates. No external API needed.
2. **Genuinely ambiguous (3/9 = 33%)** — No external evidence can uniquely resolve these without guessing.

---

## F. Safety Audit

| Invariant | Result |
|-----------|:------:|
| student_choice_bypassed_kernel | 0 ✅ |
| external_evidence_mutated_ledger | 0 ✅ |
| review_required_state_mutated | 0 ✅ |
| duplicate_resolution_application | 0 ✅ |
| unsafe_confident | 0 ✅ |
| invented_accounts | 0 ✅ |
| invented_amounts | 0 ✅ |
| invented_historical_state | 0 ✅ |
| dropped_segments | 0 ✅ |
| duplicated_segments | 0 ✅ |
| authority_conflicts | 0 ✅ |
| unbalanced_verified | 0 ✅ |
| state_leaks | 0 ✅ |
| double_mutations | 0 ✅ |
| future_transaction_executed_before_resolution | 0 ✅ |
| review_required_bypassed | 0 ✅ |
| incorrect_transaction_progression | 0 ✅ |
| duplicate_student_decision_application | 0 ✅ |
| ledger_projection_mismatch | 0 ✅ |
| incorrect_historical_resolutions | 0 ✅ |

---

## G. Determinism

| Problem | Runs | Identical | Result |
|---------|:----:|:---------:|:------:|
| "Purchased goods for 10000. Paid rent 5000." | 5 | Yes | ✅ |
| "Purchased goods from Raj Rs.50000 for cash. Paid Rs.20000 to Raj." | 5 | Yes | ✅ |
| "Purchased goods for Rs.10000 plus 18% GST, paid cash. Sold goods to Mohan for Rs.20000." | 5 | Yes | ✅ |

All determinism checks: **PASS**

---

## H. Production Changes

**Production changes: ZERO**

No production code was modified during Sprint 26. This was a pure audit sprint.

---

## I. Recommendation

### **NO TRUSTED API NEEDED YET**

**Evidence:**

1. **67% of remaining REVIEW_REQUIRED cases already have Confidence Gates** — the student can resolve them without any external data.

2. **The remaining 33% are genuinely ambiguous** — no external evidence can uniquely resolve:
   - Inclusive GST without rate (student must provide the rate)
   - Fraction references with multiple candidates in isolated transactions (already resolved by `process_problem()` with historical context)
   - Splitter limitations (deferred per Sprint 24 §8)

3. **Zero Incorrect REVIEW_REQUIRED cases** — every refusal is correct.

4. **Zero safety invariant violations** — the system is sound.

**Next steps (when ready):**

- Extend CASH_CREDIT gate to handle the "on credit from [party]" case (requires party name extraction)
- Address splitter limitations in a dedicated sprint
- The 33% genuinely ambiguous cases should remain REVIEW_REQUIRED

---

*Sprint 26 is complete. No code changes. No architecture expansion. The remaining REVIEW_REQUIRED boundary is understood and correct.*
