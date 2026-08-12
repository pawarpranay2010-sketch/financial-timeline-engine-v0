# Sprint 15H — Real-World FYJC BK Validation & Adversarial Hardening

**Status:** VERIFIED (gate `scripts/fte_fyjc_15h_test.py` → **28/28 checks PASS**)
**Scope:** FYJC Book-Keeping & Accountancy Ch.1–3 (Unit-Test-1 boundary). No
syllabus expansion. No FYJC Maths changes. No UI redesign.
**Oracle principle:** the 15H corpus is an INDEPENDENT hand-written golden set
(genuine Maharashtra-style Ch.1–3 textbook questions, classwork/homework
phrasing, teacher-style worksheets). It never consults the pattern registry
and the engine never feeds it. A benchmark case is only ever corrected by
documented oracle correction — never silently altered to make the engine pass.

---

## 1. Corpus (independent, per spec sections 1–7)

| Set | Cases | Covered spec |
|---|---|---|
| Real-question corpus (Ch.1–3) | 38 | spec 1 |
| Misleading wording matrix (standalone cases) | 7 | spec 2 |
| Convergence families (equivalent wordings) | 5 families / 21 wordings | spec 2 |
| Multi-transaction stress (chained + pronouns) | 9 | spec 3 |
| Ambiguity attack set | 12 | spec 4 |
| Student-error verification (10 single + 3 combined) | 13 | spec 5 |
| OCR / extraction boundary | 9 | spec 6 |
| Fix regressions (permanent) | 17 | spec 7 |
| **Total gate cases** | **66 reasoning + 13 + 9 + 17** | — |

Reasoning cases: **45 VERIFIED + 21 refusals** (14 REVIEW_REQUIRED, 4 BLOCKED,
3 NOT_SUPPORTED).

---

## 2. Coverage report (separate counters — never one accuracy %)

| Counter | Count |
|---|---|
| Correctly VERIFIED | 45 |
| Correctly DERIVED | 0 (the BK pipeline resolves = VERIFIED) |
| Correctly REVIEW_REQUIRED | 14 |
| Correctly BLOCKED | 4 |
| Correctly NOT_SUPPORTED | 3 |
| Incorrect confident answers | **0** |
| Incorrect refusals | **0** |
| Extraction failures | 0 |
| Parser failures | 0 |

Machine-readable: `docs/fyjc_bk_15h_coverage.json`.
Human-readable: `docs/FYJC_15H_COVERAGE.md`.

## 3. Failure taxonomy (one primary category per finding)

The only taxonomy bucket populated across the whole corpus is
`EXPECTED_REFUSAL: 21` — every refusal was the correct, intended refusal.
Zero cases fell into any failure category.

## 4. Bugs discovered & fixed (minimal, in-scope, benchmark-driven)

| # | Finding | Fix | File |
|---|---|---|---|
| 1 | `Purchased goods from Rahul for Rs.10,000. Paid him Rs.4,000` — pronoun/`worth` boundary: party regex consumed `Rahul worth` | Party/amount regex stops at `worth` | `fyjc_bk_reasoning.py` |
| 2 | Compound start `...cash Rs.60,000 and furniture worth Rs.40,000` dropped the asset (silent substitution) | Compound-start split handles `worth`-components; ambiguity → REVIEW_REQUIRED, never a silent drop | `fyjc_bk_reasoning.py` |
| 3 | `Cash withdrawn by proprietor for personal expenses` (passive drawings) refused | Passive-voice drawings wording → DRAWINGS_CASH | `fyjc_bk_reasoning.py` |
| 4 | Contradictory `for cash on credit` returned a confident cash answer | `_contradictory_cash_credit` gate → REVIEW_REQUIRED | `fyjc_bk_reasoning.py` |
| 5 | Bare `Withdrew Rs.5,000` classified NOT_SUPPORTED instead of asking for clarification | → REVIEW_REQUIRED (needs a purpose/account) | `fyjc_bk_reasoning.py` |
| 6 | `Paid wages for installation of machinery` posted Wages expense | Installation wages capitalised into the asset (CAPITALISE_EXPENSE) | `fyjc_bk_reasoning.py` |
| 7 | Aux-verb captured in subject names (`Rent was paid` → account `Rent was`) | `_strip_aux_before_verb()` in all three subject-position regex sites | `fyjc_bk_reasoning.py` |
| 8 | Passive expense refused/mis-routed (`Rent was paid in cash`) | Registry-driven passive-expense rule (expense word + aux verb + `paid`) before the receipt branch | `fyjc_bk_reasoning.py` |
| 9 | Passive cash sale refused (`Goods were sold and cash received immediately`) | `sold and cash received` / `sold and received` triggers on SALE_GOODS_CASH | `fyjc_bk_reasoning.py` |
| 10 | 15F student verifier dropped a student-supplied account `class` | `_student_lines` now carries the student class for classification checks | `fyjc_bk_15f.py` |
| 11 | Passive party payment (`Mohan was paid Rs.5,000`) journaled as a confident REVERSED receipt (Cash Dr / Mohan Cr) | Aux verb between the name and `paid` decides the direction: passive -> PAID_TO (party Dr / Cash Cr), active -> RECEIVED_FROM | `fyjc_bk_reasoning.py` |
| 12 | `Withdrew Rs.5,000 from bank for office use` (no `cash` word) refused even though the direction is structural | Structural `withdraw ... from bank` rule -> CASH_FROM_BANK; personal/private purposes stay with drawings | `fyjc_bk_reasoning.py` |
| 13 | `... in the cash book ... on credit` misread as a contradictory cash+credit mode | `cash book`/`cashbook`/`cashier` no longer count as a cash mode in `_contradictory_cash_credit` | `fyjc_bk_reasoning.py` |
| 14 | Combined student errors: `first_mistake` (15F totals-first) contradicted `error_category`/`affected_component` (15H root-cause-first) | Journal verdicts now overwrite `first_mistake` + `affected_component` from the SAME root-cause-ordered detail as the category | `fyjc_bk_15h.py` |
| 15 | `The cashier paid salaries Rs.10,000` journaled as a confident receipt (`Cash Dr / The cashier Cr`) — the subject-position expense guard had `salary` but not `salaries`, so the plural slipped past and `The cashier` was treated as a paying debtor | `salaries` added to the expense guard beside `salary`; the cashier is an agent of the firm, never a counterparty | `fyjc_bk_reasoning.py` |

All 17 fix-regression cases in `FIX_REGRESSION_CASES` are permanent regression
tests (`_run_bucket` + replay fixtures). The remediation-required contrasts are
pinned individually: the passive/active payment contrast (A: `Mohan was paid`
→ PAID_TO vs `Mohan paid` → RECEIVED_FROM, plus the `has been paid` and
`was paid the balance ... immediately` variants), the withdrawal wording
family (C: amount-first, `money`-variant and passive-voice `Cash was
withdrawn` all converge on the same CASH_FROM_BANK IR), and the cash/credit
negative set (D: `cash book`, `cash sales`, `cashier` never count as a
settlement mode, while `Purchased goods for cash on credit` stays
REVIEW_REQUIRED).

## 5. Student-error verification (spec 5)

`verify_student_with_category()` wraps the 15F verifiers and reports the
**specific** first error + affected component — never a blanket
"Incorrect answer". Categories exercised (13/13): CORRECT, WRONG_SIDE,
WRONG_ACCOUNT, MISSING_ACCOUNT, INVENTED_ACCOUNT, WRONG_AMOUNT,
JOURNAL_UNBALANCED, WRONG_CLASSIFICATION, LEDGER_ERROR, TRIAL_BALANCE_ERROR
plus three COMBINED-error answers — invented account + unbalanced journal,
wrong account + wrong amount, wrong side + wrong amount — where the gate
asserts `error_category == first_mistake == affected_component` all derive
from the SAME root-cause-ordered detail (side swap, then account presence,
then totals, then amounts, then classification). An omitted account is
reported as MISSING_ACCOUNT even though it also unbalances the journal.
Hard invariant enforced in the gate: the three diagnostic fields can never
contradict each other.

## 6. OCR / extraction boundary (spec 6)

`classify_extraction_quality()` / `process_extraction()` implement a
deterministic GOOD / UNCERTAIN → REVIEW_REQUIRED / UNUSABLE → BLOCKED gate.
9/9 controlled cases pass; a flagged unreadable digit/amount NEVER produces a
parsed number (0 invented digits).

## 7. Replay failure capture (spec 7)

Every deterministic case (45 VERIFIED + 17 fix-regressions) is captured as a
15G replay fixture (`capture_replay_fixture`) and re-executed twice plus
through serialize→deserialize→execute — **0 diverged** across 62 fixtures.
Fixtures persisted at `docs/fyjc_bk_15h_replay_fixtures.json` so a fixed
failure stays a permanent regression.

**Generated-artifact decision (LOW #6):** `docs/fyjc_bk_15h_replay_fixtures.json`
and `docs/fyjc_bk_15h_coverage.json` are **committed generated evidence** —
they are regenerated deterministically by the 15H gate on every run (the
fixtures are byte-verified at runtime, so the committed copy is evidence of
the sprint, not a cache), matching the established repo convention: the
15F gate regenerates `docs/fyjc_bk_15f_coverage.json` on every run and that
file is committed. The permanent regression pins themselves live in
`FIX_REGRESSION_CASES` (source, not generated).

## 8. Hard release gates (spec 12) — all clean

| Gate | Result |
|---|---|
| Unsafe confident answers (measured directly, not via the violations map) | **0** |
| Fabricated amounts / invented accounts | **0** |
| Silent substitutions | **0** |
| Unbalanced VERIFIED journals / trial balances | **0** |
| `formula_id=None` confident results | **0** |
| C++ authority violations | **0** |
| Replay divergence on deterministic cases | **0** |
| Lineage missing on confident outputs | **0** |
| Discrepancies silently repaired | **0** |
| Student diagnostic fields disagreeing (combined errors) | **0** |
| Passive payment confidently reversed | **0** |
| Equivalent supported wording incorrectly diverging | **0** |
| Correct refusal for genuinely ambiguous/unsupported | **21/21** |
| Existing 15E–15G regressions | green (below) |

## 9. Full regression results

| Suite | Result |
|---|---|
| Sprint 15H gate (`fte_fyjc_15h_test.py`) | **28/28 PASS** |
| Sprint 15G (`fte_fyjc_15g_test.py`) | **PASS** (worker == one-shot) |
| Sprint 15F (`fte_fyjc_bk15f_test.py`) | **43/43 PASS** |
| Sprint 15E (`fte_fyjc_bk15e_test.py`) | **30/30 PASS** |
| Sprint 15D (`fte_fyjc_15d_test.py`) | **PASS** |
| Sprint 15C P0 (`fte_fyjc_bk_p0_test.py`) | **PASS** |
| Sprint 15B (`fte_fyjc_bk15b_test.py`) | **PASS** |
| Stage 4 routing (`fte_fyjc_routing_regression_test.py`) | **PASS** |
| Student workspace UI (`fte_fyjc_student_ui_test.py`) | **PASS** |
| UI AppTest (`fte_fyjc_student_ui_apptest.py`) | **PASS** |
| Maths production gate (`fte_maths_student_production_gate_test.py`) | **PASS** |
| Readiness (`fte_fyjc_readiness_test.py`) / Pilot | **PASS** |
| C++ self-test (`formula_engine --selftest`) | **ALL OK** |
| C++ worker equivalence (`fte_fyjc_15g_cpp_benchmark.py`) | **PASS** |
| `py_compile` all touched modules | OK |
| `git diff --check` | clean |

## 10. Deliverables created

- `backend/maths/fyjc_bk_15h.py` — taxonomy, extraction gate, student-error
  categories, failure classifier, coverage report, replay fixtures,
  hard-gate scan (on top of the untouched 15G layer).
- `backend/maths/fyjc_bk_15h_benchmark.py` — the independent golden corpus.
- `scripts/fte_fyjc_15h_test.py` — the Sprint 15H gate.
- `docs/FYJC_15H_COVERAGE.md`, `docs/fyjc_bk_15h_coverage.json`,
  `docs/fyjc_bk_15h_replay_fixtures.json`, this document.
- Minimal fixes: `backend/maths/fyjc_bk_reasoning.py`,
  `backend/maths/fyjc_bk_15f.py`.

No commit/push performed — Sprint 15H stops after verification.
