# FYJC Student Maths & Book-Keeping Readiness (Sprint 13)

**Target:** FYJC students revising Mathematics + Book-Keeping & Accountancy
(BK/Accountancy) for the 24 August exam.

**Architecture invariant (unchanged from Sprint 12A-12F):** the C++ engine
(`formula_engine`) is the **sole mathematical authority**. Python ingests,
normalizes, identifies, orchestrates and explains - it never performs a
fallback financial calculation. Registered-formula maths is executed by
C++; anything the existing registries cannot cover is refused
deterministically (`UNSUPPORTED` / `BLOCKED` / `REVIEW_REQUIRED`).

---

## 1. Files

### Created (Sprint 13)

| File | Purpose |
|---|---|
| `backend/maths/fyjc_maths.py` | FYJC maths surface + `verify_maths_answer()`: question -> C++ verify -> correct/incorrect/refusal with student-readable explanation |
| `backend/maths/fyjc_accounting.py` | BK layer: golden-rule debit/credit classification, journal verification, ledger posting/balancing, trial balance, arithmetic discrepancy detection |
| `backend/maths/fyjc_question.py` | Deterministic question classification (maths / bookkeeping / unrecognised) + structured fact extraction |
| `backend/maths/fyjc_dataset.py` | Golden dataset (independent hand-verified answers - the engine never receives them) |
| `scripts/fte_fyjc_readiness_test.py` | Sprint 13 release gate (**504/504 checks**) |

### Modified (Sprint 13, additive)

| File | Change |
|---|---|
| `backend/maths/__init__.py` | Sprint 13 exports (maths surface, accounting layer, question classifier, dataset) |
| `backend/maths/normalization.py` | `Rs.`/`rs` recognized as INR; deterministic Indian lakh/crore grouping (`5,00,000` -> 500000); European-style `1.234,56` stays ambiguous (never guessed) |
| `backend/maths/student_sandbox.py` | `_attach_document_facts`: Tier-4/WEB sources are never silently upgraded (fail closed); conflicting approved-source values are preserved and flagged `conflict` instead of silently overwritten |

### Intentionally untouched
- The 12A-12F registries, solver, decision graph, agentic orchestrator, DuPont,
  reconciliation, recovery, provenance and Excel compiler.
- `formula_engine/` C++ source (no new formulas were added - Sprint 13 uses only
  what 12A-12F already compute).
- `app (1) (9).py` and any other product/UI systems (scope decision: backend +
  tests + dataset only).

---

## 2. Supported FYJC capabilities

### Maths (existing registry only - **no new formulas**)
The FYJC maths surface (`fyjc_maths_surface()`) is a read-only view over the
existing `EXTENDED_REGISTRY`. Supported relationships include (student
spellings tolerated, e.g. "roe", "return on equity"):

- Profit / Loss (and Profit as the negative of Loss)
- Gross Profit / Gross Margin / Net Margin / Operating Margin / EBITDA Margin
- Profit Margin (and its reverse: given margin + one leg, find the missing figure)
- ROA, ROE, EPS, CAGR
- Current Ratio, Quick Ratio, Working Capital
- Debt to Equity / Debt to Assets, Interest Coverage
- Asset Turnover, Equity Multiplier, Inventory / Receivables / Payables Turnover

Anything else (e.g. Simple Interest, compound interest, AP/GP sums, GST) is
refused deterministically with `UNSUPPORTED` and a student-readable
explanation - never a guessed value, never a Python fallback.

### Book-Keeping & Accountancy (deterministic golden rules)
- **Journal entries:** `classify_transaction()` maps a standard FYJC wording
  (e.g. "Purchased goods for cash Rs.10,000") to debit/credit lines using the
  modern-approach golden rules:
  - Assets & Expenses increase on the Debit side.
  - Liabilities, Capital & Income increase on the Credit side.
  - Personal accounts: debit the receiver, credit the giver.
- **Debit/credit identification** with per-account side hints.
- **Ledger posting** (`post_ledger`) and **balance verification**
  (`verify_ledger_balance`).
- **Trial balance** (`build_trial_balance` + `verify_trial_balance`):
  tally detection, missing/extra rows, wrong amounts/sides, and the exact
  arithmetic discrepancy.
- **Generic arithmetic verification** (`verify_arithmetic`): does the
  student's debit total equal their credit total?
- Accounting calculations (Gross Profit, Working Capital, margins ...) run
  through the same C++ strict path (`accounting_calculation`).

**Fail-closed rules:** a transaction whose treatment is not determinable from
the description -> `REVIEW_REQUIRED` (e.g. "Purchased goods Rs.5,000" without
cash/credit); an essential missing value (amount) -> `BLOCKED` with the exact
next step; an unreadable/ambiguous amount (OCR noise like `1,0X0`) ->
`REVIEW_REQUIRED` - never silently corrected; multiple amounts in one
sentence -> `REVIEW_REQUIRED` (submit the entry as lines instead).

---

## 3. Student-facing result states

| State | Meaning | What the student sees |
|---|---|---|
| 🟢 VERIFIED | Treatment/value determinable from approved evidence | final answer, formula/rule, verified inputs, concise working, C++ authority, source/lineage |
| 🟠 REVIEW REQUIRED | Ambiguous or conflicting - FT-E cannot safely decide | what is inconsistent, why FT-E cannot decide, what to check. **No guessed answer.** |
| 🔴 BLOCKED | Essential evidence missing | what is missing, why it is required, what to provide next (e.g. "Upload the relevant balance-sheet page or enter the verified value manually") |

Every outcome exposes: `what`, `how`, `inputs`, `where`, `status`, `why_not`,
`next_action`, `authority_state` (`cpp` / `unsupported` / `engine_unavailable`),
plus `verdict` (CORRECT / INCORRECT / REFUSED) for answer verification.

---

## 4. Student input paths

1. 📸 Photo/image of a textbook or question (OCR text -> `text`/`documents`)
2. PDF/document (extracted facts -> `documents`)
3. Typed question (`text` lines like `Revenue = 1000`)
4. Manual numeric input (`facts` / `student_answer`)

The 12D normalization pipeline handles ₹, `Rs.`, commas, percentages,
negative numbers, parentheses, lakh/crore grouping and OCR uncertainty -
never silently correcting an uncertain value.

**Evidence tiers (unchanged from 12C/12D):** Tier 1 primary document,
Tier 2 user-uploaded appendix, Tier 3 approved structured API, Tier 4
FORBIDDEN. Conflicting evidence preserves both values and yields
`REVIEW_REQUIRED` / `EVIDENCE_CONFLICT`; missing evidence yields `BLOCKED`,
never an invented value.

---

## 5. Golden dataset

`backend/maths/fyjc_dataset.py` holds hand-verified cases (independent oracle
- the engine never receives the expected answers):

- 18 maths cases (forward, reverse, wrong answer, missing input, ambiguous
  input, unsupported formula, negative values, percentages, lakh grouping)
- 20 transaction classification cases (15 golden rules + missing amount +
  ambiguity + multi-amount + OCR noise)
- 7 journal verification cases
- 3 ledger balance cases + ledger scenario with hand-computed balances
- 4 trial-balance cases (correct, missing row, wrong amount, discrepancy)
- 11 question classification cases (incl. unrecognised -> refused)
- 5 student acceptance workflow cases (correct & incorrect work, refusal)

---

## 6. Verification summary

Run the gate: `python3 scripts/fte_fyjc_readiness_test.py`

| Area | Result |
|---|---|
| FYJC readiness gate (504 checks, incl. embedded 12A-12F regression) | **504/504 PASS** |
| C++ mathematical authority | active; supported maths routed to C++; strict path BLOCKs without the binary (`ENGINE_UNAVAILABLE`), no Python fallback |
| 12A-12F regression (rerun inside the gate) | unchanged (see suite headers) |

---

## 7. Limitations & unsupported FYJC topics (explicit)

- **Maths:** only the relationships the existing 12A-12F registries compute
  are supported. Simple/compound interest, GST, ratio/proportion beyond the
  registered financial ratios, AP/GP, coordinate geometry, trigonometry,
  probability, statistics and similar FYJC topics are **not** implemented and
  are refused deterministically (`UNSUPPORTED`).
- **Accountancy:** only the golden rules and accounts in the FYJC chart are
  classified. Complex adjustments (depreciation schedules, bad-debt
  provisions, GST-credited purchases, suspense accounts, rectification of
  errors, final accounts preparation) are out of scope and refused or marked
  REVIEW_REQUIRED rather than guessed.
- Multi-transaction sentences are refused (`REVIEW_REQUIRED`) rather than
  guessed; students are directed to submit each entry separately or as
  journal lines.
- Student usability testing with real FYJC students (Sprint 13 section 8)
  is a manual step to be run with trusted students before the exam; this
  sprint delivers the automated acceptance benchmark and the protocol in
  section 8 of the sprint brief.
- Determinism: repeated identical inputs produce identical outputs
  (verified by the gate's Part T over 4 full-dataset runs).
