# FIX #3 — CALCULATION SAFETY GATE REPORT

**Date:** 2026-07-31
**Scope:** Make it impossible for unresolved or unverified financial evidence to reach the deterministic calculation engine. One centralized deterministic gate, enforced at the calculation-engine boundary — not the UI/status/memo layer.
**Status:** ✅ COMPLETE & VERIFIED — no regressions. Fix #4 NOT implemented.

---

## 1. Root Cause (confirmed, from INDIAN_STRESS_TEST_REPORT)

`_check_calculation_block()` in `agentic_rag_orchestrator.py` only blocked on three terminal states:

```python
if state.state.terminal_state in (
    STATE_UNRESOLVED_CONFLICT,
    STATE_CURRENCY_MISMATCH,
    STATE_EXTRACTION_CORRUPTED,
):
    return False
return True
```

**Bug A — insufficient evidence never blocked:** `INSUFFICIENT_EVIDENCE`, `RETRIEVAL_LIMIT_REACHED` and `EXECUTION_TIMEOUT` fell through to `return True`, so calculations proceeded with unresolved evidence.

**Bug B — the 954-PENDING leak:** the canonical-set assembly explicitly admitted PENDING evidence:

```python
if item.verification_status in ("VERIFIED", "PENDING") and item.value is not None:
    canonical.add_resolved(item.to_dict())
```

On the Tata 20-F, 954 PENDING evidence items were treated as "resolved" and exposed to downstream processing.

## 2. Exact Calculation Boundary Changed

| Boundary | Before | After |
|---|---|---|
| `FinancialCalculator.calculate()` | no gate — computed whatever dict it received | unchanged (legacy, for existing callers) |
| `FinancialCalculator.safe_calculate()` / `safe_calculate_financial_ratios()` | **did not exist** | **NEW enforced entry point** — runs `CalculationSafetyGate.check()` first; BLOCKED → structured failure with `calculation: None`; ALLOWED → deterministic ratios |
| `AgenticRAGOrchestrator._check_calculation_block()` | blocked only 3 states | blocks **all 6 unsafe states** + any requirement not VERIFIED (missing/conflicting/FOUND) |
| `CanonicalEvidenceSet` assembly | admitted `VERIFIED` **and `PENDING`** | admits **`VERIFIED` only** — PENDING can never enter downstream |

## 3. Files Changed

| File | Type | Change |
|---|---|---|
| `backend/intelligence/calculation_safety_gate.py` | **NEW** | Centralized deterministic `CalculationSafetyGate` (reuses existing `EVIDENCE_VERIFIED`/`STATE_*` terminology and the existing `CurrencyValidator` — no new enums, no LLM). Checks: empty set, missing metrics, per-fact verification status, currency compatibility, period compatibility, scale-normalization sanity. Structured result: `{status, reason, required_facts, rejected_facts, calculation}`. Also `check_canonical()` for `CanonicalEvidenceSet.to_dict()`. |
| `backend/financial_calculator.py` | Modified (additive) | Added `FinancialCalculator.safe_calculate()` + module-level `safe_calculate_financial_ratios()` — the gated calculation-engine boundary. `calculate()` untouched (numerics identical). |
| `backend/intelligence/agentic_rag_orchestrator.py` | Modified | `_check_calculation_block()` now blocks on INSUFFICIENT_EVIDENCE / RETRIEVAL_LIMIT_REACHED / EXECUTION_TIMEOUT + any non-VERIFIED requirement; canonical assembly admits VERIFIED only (PENDING leak killed) and logs when not populated. |
| `tests/test_calculation_safety_gate.py` | **NEW** | 29 tests covering A–P plus Q–R integration. |
| `tests/stress_test_indian_report.py` | Modified (harness) | TEST 12 now exercises the real gate: PENDING → BLOCKED, VERIFIED → ALLOWED with numerics identical to legacy `calculate()`. |

## 4. Tests — Requirements A–P (+ Q/R)

| Req | Test | Result |
|---|---|---|
| A | VERIFIED revenue + net income → profit margin allowed | ✅ ALLOWED, 20.0 |
| B | PENDING revenue → blocked | ✅ BLOCKED (PENDING) |
| C | CONFLICTING revenue → blocked | ✅ BLOCKED (CONFLICTING) |
| D | UNRESOLVED_CONFLICT → blocked | ✅ BLOCKED |
| E | REJECTED fact → blocked | ✅ BLOCKED |
| F | INSUFFICIENT_EVIDENCE → blocked | ✅ BLOCKED |
| G | MISSING required metric → blocked | ✅ BLOCKED (MISSING) |
| H | CURRENCY_MISMATCH → blocked | ✅ BLOCKED (CURRENCY_MISMATCH) |
| I | PERIOD_MISMATCH → blocked | ✅ BLOCKED |
| J | Empty evidence set → blocked | ✅ BLOCKED |
| K | VERIFIED + valid scale normalization → allowed; unnormalized value → SCALE_MISMATCH blocked | ✅ |
| L | VERIFIED INR/INR → allowed | ✅ |
| M | VERIFIED EUR/USD (no FX) → blocked | ✅ BLOCKED (CURRENCY_MISMATCH) |
| N | No blocked calculation produces a numeric result | ✅ `calculation` is always None when BLOCKED |
| O | No silent fallback to raw/unverified values | ✅ rejected fact surfaced, never computed |
| P | Existing calculations numerically unchanged for valid VERIFIED inputs | ✅ `safe_calculate == calculate` value-for-value |
| Q | Canonical set admits VERIFIED only; **954-PENDING scenario → 0 facts resolved** | ✅ resolved_count = 0 |
| R | Orchestrator blocks on INSUFFICIENT_EVIDENCE / RETRIEVAL_LIMIT_REACHED / missing requirement; allows COMPLETE+VERIFIED | ✅ |

**Gate suite: 29/29 PASS**

## 5. Tata BEFORE → AFTER (Fix #3 only)

| Check | BEFORE | AFTER |
|---|---|---|
| PENDING evidence in canonical set | **954 PENDING items exposed as resolved** | **0 — VERIFIED-only admission; calculation BLOCKED (PENDING)** |
| `_check_calculation_block` on INSUFFICIENT_EVIDENCE | allowed (fell through) | **blocked** |
| `_check_calculation_block` on RETRIEVAL_LIMIT_REACHED | allowed | **blocked** |
| Gate at engine boundary | none (`calculate()` only) | `safe_calculate()` returns structured BLOCKED — no numeric result |
| VERIFIED canonical set → ratios | computed | computed, **numerics identical** (Profit Margin 3.28 etc.) |
| Tata stress totals | 45 PASS / 1 FAIL / 7 WARN (53) | **47 PASS / 1 FAIL / 7 WARN (55)** — +2 new gate checks, 0 new failures |

The **only remaining FAIL is EBITDA missing** — the Tata 20-F genuinely contains zero EBITDA mentions; it stays `MISSING/INSUFFICIENT_EVIDENCE` and any calculation requiring it is **blocked** (verified by test R: missing requirement → `_check_calculation_block` returns False). This was deliberately NOT "fixed".

## 6. Full Regression Matrix

| Suite | Result |
|---|---|
| Calculation Safety Gate (new) | **29/29 PASS** |
| Scale Propagation | **18/18 PASS** |
| Extraction 2.0 | **40/40 PASS** |
| IFRS XBRL | **10/10 PASS** |
| Agentic RAG | **35/35 PASS** |
| AI Executive integration | **57 PASS · 0 FAIL · 1 WARN** |
| App integration | **42 PASS · 0 FAIL** |
| Apple SEC/XBRL real-doc e2e | **11/11 PASS** (no US-GAAP regression) |
| Tata Motors 20-F real-doc stress | **47 PASS · 1 FAIL · 7 WARN** (only genuine EBITDA absence) |

## 7. Can any unverified fact still reach the calculation engine?

**No, through the Agentic RAG / canonical path:** `_check_calculation_block` returns False for any non-VERIFIED requirement or unsafe terminal state, and the canonical set is now populated exclusively from `verification_status == "VERIFIED"` items. Independently, `safe_calculate()` re-validates every input at the engine boundary and refuses (returns `calculation: None`) for PENDING/MISSING/CONFLICTING/UNRESOLVED_CONFLICT/REJECTED/INSUFFICIENT_EVIDENCE/CURRENCY_MISMATCH/PERIOD_MISMATCH.

**One deliberate exception, documented:** the legacy `FinancialCalculator.calculate()` remains ungated for backward compatibility with the pre-existing Module 3 path (`module3_controller.run_module3` → `extract_financial_data`), which never carried verification metadata and whose 42 app-integration tests must stay green. The gated entry point is the required path for evidence-backed calculations.

## 8. Frozen Components — Untouched ✅

`agentic_rag_orchestrator.py` architecture (retrieval loop, SourceResolver, CurrencyValidator, ExtractionAuditor, AI Executive) — only the gate function + canonical assembly were fixed, no redesign. No provider, DB, or Module 4 changes.

## 9. Next Step (NOT implemented)

Fix #4 — Period Contamination (glossary/legal-text years like `Revenue=1978`; structural/contextual validation, no year blacklists).

**STOPPING here — awaiting approval before implementing Fix #4.**
