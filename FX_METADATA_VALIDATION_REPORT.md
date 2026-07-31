# FIX #5 — FX METADATA VALIDATION & CURRENCY SAFETY REPORT

**Date:** 2026-07-31
**Scope:** Fix #5 only (FX metadata validation & currency safety). Fixes #1–#4 verified prior.
**Architecture:** Frozen — AgenticRAGOrchestrator, EvidenceSummaryState, SourceResolver, ExtractionAuditor, AI Executive, provider routing, retrieval loop, database technology, existing calculation formulas **all untouched**.

---

## 1. AUDIT — Currency/FX Metadata Trace (no changes made during audit)

```
XBRL / Table / Text extraction
   └─ financial_extractor_v2.py
        · currency_code  ✓ created (XBRL: x.unit; table: table.currency; text: _currency_in_text)
        · currency_role  ✓ created (hardcoded "REPORTING" — conservative default)
        · fx_rate/fx_source/fx_timestamp ✓ created (always None — extractor never invents FX)
   │
   ▼  FinancialFact → EvidenceItem  (to_evidence_item_dict)
        ✗❌ FX fields DROPPED — mapping only carried currency_code + currency_role
   │
   ▼  EvidenceSummaryState
        ✗❌ EvidenceItem dataclass had NO fx_rate/fx_source/fx_timestamp fields
        ⚠️ compute_evidence_hash included currency_code but NOT currency_role
   │
   ▼  CanonicalEvidenceSet (orchestrator add_resolved(item.to_dict()))
        ⚠️ inherited the EvidenceItem drop
   │
   ▼  CalculationSafetyGate → FinancialCalculator
        ✓ gate step 4 reused CurrencyValidator.check_currency_compatibility
        ✗❌ validator treated any non-None fx_rate as "valid FX metadata"
```

### Confirmed defects (all reproduced in tests)

| # | Defect | Location | Violated requirement |
|---|--------|----------|----------------------|
| 1 | `fx_rate/fx_source/fx_timestamp` dropped at `to_evidence_item_dict` | `financial_extractor_v2.py` | §6 Prevent currency metadata loss |
| 2 | `EvidenceItem` had no FX fields; `to_dict()` omitted them | `evidence_summary_state.py` | §6 Prevent currency metadata loss |
| 3 | `fx_rate is not None` ⇒ compatible — `fx_source=None` still passed | `currency_validator.py is_compatible_with` | Case D — missing FX source → reject |
| 4 | Missing `fx_timestamp` never checked | `currency_validator.py` | Case E — missing FX timestamp → reject |
| 5 | Zero / negative / NaN / infinity / non-numeric rates all accepted as "valid" | `currency_validator.py` | Case F — invalid FX rate → `INVALID_FX_METADATA` |
| 6 | No FX freshness hook/state existed | `currency_validator.py` | Case G — deterministic freshness state |
| 7 | XBRL `currency_code = x.unit` — per-share units (`USD/shares`) polluted the code | `financial_extractor_v2.py` XBRL path | §2/§6 — bare ISO code only |
| 8 | Dedup hash ignored `currency_role` — two facts with same value/ccy but different roles could collapse | `evidence_summary_state.py compute_evidence_hash` | §3 roles semantically distinct |

**Not defects (verified working):**
- DB layer: `ExtractedFact` already has `currency_code/currency_role/fx_rate/fx_source/fx_timestamp` columns; `database_manager.save_extracted_fact` maps all five. ✅
- `FinancialCalculator.calculate()` legacy path untouched; `safe_calculate()`/`safe_calculate_financial_ratios()` remain the enforced gated boundary (Fix #3). ✅
- Five roles (`REPORTING/FUNCTIONAL/PRESENTATION/TRANSACTION/TAX`) already distinct in `ALL_ROLES`. ✅

---

## 2. CHANGES MADE (minimal, additive only)

| File | Change |
|------|--------|
| `backend/intelligence/evidence_summary_state.py` | Added `fx_rate/fx_source/fx_timestamp` fields to `EvidenceItem` (additive, same pattern as Fix #2 scale fields); included them in `to_dict()`; added `currency_role` + `fx_rate` to `compute_evidence_hash` content (role-differentiated facts can no longer dedup-collapse) |
| `backend/extraction2/financial_extractor_v2.py` | `to_evidence_item_dict` now passes through `fx_rate/fx_source/fx_timestamp`; added `_currency_from_unit()` — XBRL `currency_code` is now the bare ISO code (`USD/shares` → `USD`, `shares`/`pure` → `""`) |
| `backend/intelligence/currency_validator.py` | Added `INVALID_FX_METADATA`, `FX_METADATA_VALID`, `FX_FRESHNESS_UNCONFIGURED`, `FX_STALE`, `FX_FRESH` states; `validate_fx_rate()` (rejects None/zero/negative/NaN/inf/non-numeric); `validate_fx_metadata()` (rate + source + timestamp all required — Cases D/E/F); `check_fx_freshness()` deterministic hook (Case G — `FRESHNESS_UNCONFIGURED` when no policy, no invented threshold); `_has_any_fx_metadata()` (Case B: no FX metadata ⇒ `CURRENCY_MISMATCH`; broken FX metadata ⇒ `INVALID_FX_METADATA`); `fx_compatibility_state()` for the gate; `convert_fact()` explicit conversion preserving original + full audit trail (never auto-called) |
| `backend/intelligence/calculation_safety_gate.py` | Step 4 now uses `fx_compatibility_state()` — surfaces distinct `INVALID_FX_METADATA` block reason vs `CURRENCY_MISMATCH` |
| `tests/test_fx_metadata_validation.py` | **NEW — 35 tests** covering requirements A–G |
| `tests/test_agentic_rag.py` | Test-fixture update only: `test_fx_metadata_makes_compatible` now includes `fx_timestamp` on both facts (the new Case E policy requires it; the test's intent — valid FX metadata enables compatibility — is unchanged) |
| `tests/stress_test_indian_report.py` | Added TEST 6b — Fix #5 measurement (7 records) to the Tata real-document harness |

**Zero year/currency blacklists. No LLM decision-making. No silent conversion anywhere.**

---

## 3. BEHAVIOR MATRIX (per Fix #5 cases)

| Case | Input | Result |
|------|-------|--------|
| A | USD/USD, INR/INR, EUR/EUR | ✅ compatible |
| B | EUR/USD, INR/USD, USD/GBP (no FX) | ✅ `CURRENCY_MISMATCH` → calculation blocked |
| C | EUR/USD with valid rate+source+timestamp on both | ✅ compatible (conversion permitted only via explicit `convert_fact`) |
| D | fx_rate present, fx_source missing | ✅ `INVALID_FX_METADATA` → blocked |
| E | fx_rate + fx_source present, fx_timestamp missing | ✅ `INVALID_FX_METADATA` → blocked |
| F | rate = 0 / −1.5 / NaN / inf / "abc" | ✅ `INVALID_FX_METADATA` (rate must be positive finite number) |
| G | freshness hook | ✅ `FRESHNESS_UNCONFIGURED` (no policy) / `FX_FRESH` / `FX_STALE` (with policy); no arbitrary threshold invented |
| H | USD REPORTING vs USD FUNCTIONAL | ✅ compatible — code compatibility separated from role semantics |

---

## 4. TEST RESULTS

### New Fix #5 suite — `tests/test_fx_metadata_validation.py`: **35/35 PASS**

| Group | Tests | Result |
|-------|-------|--------|
| A. Same-currency compatibility | 3 | ✅ |
| B. Currency mismatch | 4 | ✅ |
| C. Role handling | 3 | ✅ |
| D. FX metadata (missing/invalid rate/source/timestamp; gate reason) | 10 | ✅ |
| E. Conversion + audit trail | 3 | ✅ |
| F. Ratio safety (EUR revenue/USD income, ROE, debt/equity) | 4 | ✅ |
| G. Boundary propagation (EvidenceItem, extractor mapping, XBRL unit sanitize, state roundtrip, freshness, dedup hash) | 8 | ✅ |

### Full regression matrix (all green, zero regressions)

| Suite | Result |
|-------|--------|
| Extraction 2.0 | **40/40** |
| IFRS XBRL | **10/10** |
| Scale Propagation | **18/18** |
| Period Association (Fix #4) | **19/19** |
| Calculation Safety Gate (Fix #3) | **29/29** |
| Agentic RAG | **35/35** (fixture updated for Case E policy) |
| AI Executive | **57 PASS / 0 FAIL / 1 WARN** (pre-existing warn) |
| App Integration | **42/42** |
| Apple SEC real-document E2E | **11/11** |
| **Tata Motors 20-F real-document stress** | **56 PASS / 1 FAIL / 8 WARN** (+7 TEST 6b records, all ✅) |

### Tata TEST 6b records (real-document harness)

```
[✅] INR/INR VERIFIED profit margin allowed
[✅] EUR revenue / USD income BLOCKED — CURRENCY_MISMATCH
[✅] Zero FX rate → INVALID_FX_METADATA — INVALID_FX_METADATA
[✅] Valid FX metadata pair compatible — COMPATIBLE
[✅] Freshness hook → FRESHNESS_UNCONFIGURED (no invented threshold)
[✅] Explicit EUR→USD conversion preserves audit trail —
     {original_value: 100, original_currency: EUR, original_role: REPORTING,
      rate: 1.08, source: ECB, timestamp: 2026-01-01 00:00:00+00:00,
      target_currency: USD, converted_value: 108.0,
      freshness_state: FRESHNESS_UNCONFIGURED}
[✅] USD REPORTING vs USD FUNCTIONAL not a conflict
```

### Bugs discovered during implementation
1. **Strictness conflation bug** (found by my own F-group tests): facts with *no* FX metadata at all and facts with *broken* FX metadata both returned `INVALID_FX_METADATA`. Fixed by `_has_any_fx_metadata()`: no metadata ⇒ `CURRENCY_MISMATCH` (Case B); present-but-broken ⇒ `INVALID_FX_METADATA` (Cases D/E/F).
2. **Decorator regression** (found by Extraction/Scale/Period suites): inserting `_currency_from_unit` before `_has_currency` consumed its `@staticmethod` decorator → `TypeError: takes 1 positional argument but 2 were given` across 42 tests. Restored the decorator; all suites green again. Caught and fixed before any report was produced — no test was weakened.

---

## 5. TATA BEFORE → AFTER (currency/FX behavior)

| Aspect | BEFORE (Fix #4 state) | AFTER (Fix #5) |
|--------|----------------------|----------------|
| EvidenceItem FX fields | **absent** (dropped at conversion) | present end-to-end (`fx_rate/fx_source/fx_timestamp` survive to `to_dict`) |
| XBRL per-share currency | `currency_code="USD/shares"` (never equals `USD`) | `currency_code="USD"`, scale `per-share` preserved separately |
| EUR/USD ratio | blocked (`CURRENCY_MISMATCH`) | still blocked — identical behavior |
| INR/INR legitimate calc | allowed | allowed — Profit Margin verified (`ALLOWED`) |
| FX metadata validation | `fx_rate is not None` ⇒ pass | complete rate+source+timestamp required; invalid ⇒ `INVALID_FX_METADATA` |
| Freshness | no signal | deterministic `FRESHNESS_UNCONFIGURED` / `FRESH` / `STALE` hook |
| Conversion | not possible | explicit `convert_fact()` with full audit trail; never automatic |
| Stress totals | 48 PASS / 1 FAIL / 8 WARN (Fix #4 end) | **56 PASS / 1 FAIL / 8 WARN** (+7 Fix #5 records, 0 new failures) |

The single remaining FAIL (`EBITDA` missing from the Tata 20-F) is the **genuine absence** — correctly kept `MISSING`, not hallucinated, calculation blocked per Fix #3.

---

## 6. PRODUCTION-READINESS ASSESSMENT

### ✅ PASS
- All FX compatibility rules A–H implemented deterministically (no LLM)
- Invalid FX metadata (`INVALID_FX_METADATA`) and currency mismatch (`CURRENCY_MISMATCH`) both block at the calculation-engine boundary
- FX metadata survives every boundary: XBRL → FinancialFact → EvidenceItem → EvidenceSummaryState → CanonicalEvidenceSet → Gate
- Explicit conversion preserves original + audit trail (`original_fact`, `fx_conversion`); no silent conversion anywhere
- Same-currency legitimate calculations unchanged and numerically identical
- Full regression matrix green incl. both real documents (Apple SEC 11/11, Tata 56 PASS)

### ⚠️ WARN
- **FX freshness policy not configured** — `check_fx_freshness()` returns `FRESHNESS_UNCONFIGURED`; a production conversion policy (max age + allowed sources) must be chosen by the business before automatic conversion is enabled. This is by design (no invented threshold); the hook + state exist.
- **Currency roles in extraction default to REPORTING** — the extractor labels every fact `REPORTING` (conservative). Documents that explicitly disclose FUNCTIONAL/PRESENTATION/TRANSACTION/TAX roles for specific facts are not yet auto-tagged; the validator supports all five roles and will enforce them correctly once tagged.
- **Tata evidence anchors 85.6% resolvable** — pre-existing warning, unchanged by Fix #5 (documented in Fix #4 report).

### ❌ FAIL
- 0 code failures. The only stress FAIL is the genuine EBITDA absence (correctly blocked, not "fixed").

### Remaining production blockers
1. Choose and configure the FX freshness policy (max age, trusted sources) — currently `FRESHNESS_UNCONFIGURED`.
2. (Optional, non-blocking) Document-level currency-role tagging for multi-currency filings.

---

## 7. VERDICT

Fix #5 is **COMPLETE & VERIFIED**. FX metadata is now validated deterministically end-to-end; incompatible-currency calculations are impossible; conversion (when it arrives) is explicit and fully auditable. No regressions in any suite or real-document test.

**STOPPING as instructed — Fix #5 is the final approved fix of the five. No UI, chatbot, standalone website, or other features were touched. Awaiting your direction.**
