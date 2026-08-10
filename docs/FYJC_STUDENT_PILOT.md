# FYJC STUDENT PILOT — SPRINT 15 STAGE-1 REPORT

**Financial Timeline Engine (FT-E) · Sprint 15 · 2026-08-10**
**Stage 1 of the pilot progression: Internal benchmark — 40 real FYJC questions.**

The sprint question is not «Can FT-E solve FYJC questions?» but
**«Can an FYJC student use FT-E independently, trust the result for the right
reasons, verify it themselves, and recognize when FT-E should not answer?»**

This document is the Stage-1 evidence base for that question. It reports what
the engine honestly does on 40 authentic FYJC-style questions, classifies every
mismatch instead of hiding it, and records the blockers that must be fixed
before any student is handed the tool.

---

## 1. Verdict

> **CONDITIONAL PASS — FIX SPECIFIC BLOCKERS**

All 12 safety invariants hold, all 20 Book-Keeping cases pass, all 9 expected
refusals refuse correctly, and every resolved number flows through the C++
mathematical authority (8/8). **However, 2 of 40 questions produce an UNSAFE
confident answer** — a wrong metric answered confidently — which violates the
0% unsafe-answer-rate target. These are blockers, listed in §6.

---

## 2. Dataset

Source: `backend/maths/fyjc_pilot_dataset.py` (pure data — no engine imports,
no computation). Every expected value is an independent, hand-verified constant
(an oracle that never calls the solver). Copyrighted textbook content is not
reproduced — only the minimal exam-style question representation needed to
validate the system.

| Dimension | Value |
|---|---|
| Maths questions | 20 (P01–P20) |
| Book-Keeping questions | 20 (B01–B20) |
| **Total** | **40** |
| Maths source kinds | typed, photo, pdf, textbook-style |
| BK source kinds | typed, photo |
| Difficulty (maths) | easy 8 · medium 9 · hard 3 |
| Difficulty (BK) | easy 10 · medium 7 · hard 3 |
| Maths topics | Percentages · Profit/Loss · Ratio & Proportion · Simple Interest · Compound Interest · AP · GP · GST · Shares/Dividend/Commission · Reverse calculation · Conflicting evidence |
| BK kinds | transaction (11) · ledger · trial_balance · verify_journal (2) · verify_ledger · verify_tb · missing-info · ambiguous · discount |

Deliberate mixture included: clean questions, photo/PDF-style inputs, multi-step
questions, unnecessary information (P01 prose), missing information (P17, P18),
ambiguous transactions (B18), conflicting evidence (P19), unsupported topics
(P12–P17), and common student mistakes (P03 variant, B14, B16, B20).

---

## 3. Accuracy matrix (Stage 1 results)

Run with `python3 scripts/fte_fyjc_pilot_test.py`.

### Maths (20)

| ID | Topic | Expected | Actual | Match | Classification |
|----|-------|----------|--------|:----:|----------------|
| P01 | Profit/Loss (prose) | DERIVED 4000.00 | BLOCKED | ✗ | Extraction failure — narrative prose has no `Concept: value` lines, so the 12D normalizer reads nothing; FT-E refuses rather than guesses (correct behaviour, gap: prose parsing) |
| P02 | Loss | DERIVED 200.00 | DERIVED 200.00 | ✓ | — |
| P03 | Profit Margin | DERIVED 20.00% | DERIVED 20.00% | ✓ | — (mistake variant 25 → INCORRECT ✓) |
| P04 | Current Ratio | DERIVED 2.00 | DERIVED 2.00 | ✓ | — |
| P05 | ROE | DERIVED 20.00% | BLOCKED | ✗ | Interpretation failure — classifier routes `ROE` to `profit`; refuses with the wrong missing-list (no wrong answer, wrong reason) |
| P06 | EPS | DERIVED 2.00 | BLOCKED | ✗ | Interpretation failure — same misroute to `profit` |
| P07 | Reverse (Expenses) | DERIVED 800.00 | **VERIFIED 200.00** | ✗ | **UNSAFE confident answer** — asks for Expenses, engine answers Profit 200.00 with a C++-verification banner |
| P08 | Reverse (Profit) | DERIVED 200.00 | **VERIFIED 20.00** | ✗ | **UNSAFE confident answer** — asks for Profit, engine answers Profit Margin 20.00 |
| P09 | Gross Profit | DERIVED 4000.00 | DERIVED 4000.00 | ✓ | — |
| P10 | Debt to Equity | DERIVED 0.50 | DERIVED 0.50 | ✓ | — |
| P11 | Profit (photo-style) | DERIVED 4000.00 | DERIVED 4000.00 | ✓ | — |
| P12 | Simple Interest | UNSUPPORTED | UNSUPPORTED | ✓ | Correct refusal (no registered formula) |
| P13 | Compound Interest | UNSUPPORTED | UNSUPPORTED | ✓ | Correct refusal |
| P14 | AP | UNSUPPORTED | UNSUPPORTED | ✓ | Correct refusal |
| P15 | GP | UNSUPPORTED | UNSUPPORTED | ✓ | Correct refusal |
| P16 | GST | UNSUPPORTED | UNSUPPORTED | ✓ | Correct refusal |
| P17 | Dividend | UNSUPPORTED | UNSUPPORTED | ✓ | Correct refusal |
| P18 | ROE (missing Equity) | BLOCKED | BLOCKED | ✓ | Correct refusal (reason cites the wrong metric's inputs — see §6) |
| P19 | Conflicting Current Assets | REVIEW_REQUIRED | REVIEW_REQUIRED | ✓ | Conflict never merged; no silent choice |
| P20 | Zero denominator | BLOCKED | BLOCKED | ✓ | No division by zero, no guess |

### Book-Keeping (20) — **20/20 PASS**

B01–B11 transactions (journal, golden rule, accounts, amounts incl. discount
allowed) · B12 ledger posting with independent balances + posting totals
75,000/75,000 · B13 trial balance 65,000/65,000 tally · B14 trial-balance error
flagged INCORRECT · B15/B16 journal-entry verification (CORRECT and INCORRECT
student work) · B17 missing amount → BLOCKED with the accounts identified ·
B18 cash/credit ambiguity → REVIEW_REQUIRED · B19 trade-discount netting →
REVIEW_REQUIRED (documented capability gap, §7) · B20 ledger side error flagged
INCORRECT.

### Metrics

| Metric | Value |
|---|---|
| Maths supported accuracy | 6/11 (54.5%) |
| Maths correct-refusal rate | 9/9 (100%) |
| Book-Keeping accuracy | 20/20 (100%) |
| C++ numerical match rate | 8/8 resolved (100%) |
| **Unsafe confident answers** | **2** (P07, P08) |
| Determinism | identical output on repeat runs |

---

## 4. Safety invariants — all 12 verified

1. **No fabricated values** — BLOCKED/UNSUPPORTED never carry a display. ✓
2. **No silent substitutions** — conflicts surface REVIEW_REQUIRED (P19). ✓
3. **No unsupported formula executed** — every resolved output ran a registered
   uppercase formula ID or re-used a stated input; never an invented formula. ✓
4. **No Python mathematical fallback** — resolved outputs carry
   `authority_state: cpp` (8/8). ✓
5. **No conflicting facts silently merged** — P19 stays REVIEW_REQUIRED. ✓
6. **Missing inputs → BLOCKED** — P18, B17. ✓
7. **Ambiguous inputs → REVIEW_REQUIRED** — B18, B19. ✓
8. **Unsupported questions → UNSUPPORTED** — P12–P17. ✓
9. **C++ remains the mathematical authority** — 8/8 resolved. ✓
10. **Student-visible reasoning matches execution** — every resolved flow emits
    the 6-step journey (Given/Required/Formula/Substitution/C++/Final Answer). ✓
11. **Independent verification agrees** — verdict checks (correct + incorrect
    student work) across maths and book-keeping. ✓
12. **Identical inputs → deterministic output** — repeat-run fingerprints
    identical. ✓

---

## 5. What works well (protect this)

- **Book-keeping is fully correct** — 20/20 including verification of
  student-submitted journal entries, trial balances and ledger balances, with
  the exact discrepancy stated, never a guess.
- **The refusal machinery is honest** — SI/CI/AP/GP/GST/dividend questions,
  missing amounts, zero denominators and ambiguous transactions all refuse
  deterministically with a student-readable reason.
- **C++ authority holds** — no Python fallback calculation exists anywhere in
  the resolved path.
- **Determinism** — identical inputs produce identical outputs on every run.

---

## 6. Blockers (Stage 4 candidates — fix only high-impact)

### Blocker A — unsafe confident answers on reverse/"find the missing figure" questions (P07, P08)

A student asks *«Find the missing figure: Expenses»* — FT-E's classifier routes
the request to `profit`, the graph echoes the given `Profit: 200` fact (no
formula runs), and the UI shows a confident **«⚙️ C++ verified — 200.00»**
answer to a question whose answer is 800. Same class on P08 (asks for Profit,
gets Profit Margin 20.00).

- Impact: a student can copy a wrong, confidently-presented answer into an
  assignment/exam — the exact failure Sprint 15 exists to prevent.
- Fix direction (Stage 4): the classifier must resolve the REQUESTED metric
  from the question intent before solving, and a requested metric that is
  already a stated input must be treated as a reverse-calculation request
  (solve for the missing variable) or refused — never echoed as "derived".
- Target: unsafe-answer rate back to 0%.

### Blocker B — ROE/EPS route to `profit` (P05, P06, P18)

`Calculate ROE` / `Calculate EPS` are classified as `profit`, so FT-E asks for
Revenue/Expenses instead of Equity/Shares Outstanding. It refuses correctly
(status BLOCKED) but for the wrong reason — the student cannot supply the right
inputs and does not learn what is missing.

- Impact: supported metrics are unreachable; refusal reasons mislead.
- Fix direction (Stage 4): add ROE/EPS (and reverse-variant) intent to the
  classifier; keep the solver untouched.

### Blocker C (documented, not a code blocker) — prose extraction gap (P01)

Narrative questions without `Concept: value` lines are not parsed → BLOCKED.
This is honest, but a real student types prose. UX option for a later sprint: a
guided "we could not read the numbers — enter them here" re-entry card
(equivalent to the BLOCKED UI's «Enter Value Manually»).

### Blocker D (documented capability gap) — trade-discount netting (B19)

`10% trade discount` on a credit purchase is refused (REVIEW_REQUIRED) because
no registered formula nets the discount. The independent answer (₹9,000) is
recorded in the dataset; per Sprint 15 rules this gap is documented, not
silently added.

---

## 7. Capability-gap log (recorded, not patched)

| # | Gap | Evidence | Decision |
|---|-----|----------|----------|
| 1 | Reverse/"missing figure" questions misroute and echo inputs | P07, P08 | Stage 4 blocker (unsafe) |
| 2 | ROE / EPS intent not classified | P05, P06, P18 | Stage 4 blocker |
| 3 | Prose (non-`Concept: value`) extraction | P01 | UX re-entry card, later sprint |
| 4 | Trade-discount netting (₹10,000 − 10% → ₹9,000) | B19 | Documented; no formula added |
| 5 | Simple/Compound Interest, AP, GP, GST, dividend | P12–P17 | Correctly refused; registry unchanged |
| 6 | Verify-journal / verify-trial-balance questions typed as a question are not auto-classified (go REVIEW_REQUIRED); the verification functions themselves are correct when driven from the Verify-Yourself input | B14–B16, B20 | UI finding to record in Stage 3 |

---

## 8. Student pilot protocol (Stages 2–7)

### Stage 2 — Personal use
Use FT-E on real revision questions; log every refusal and confusion point.

### Stage 3 — Zero-Hint Protocol (3–5 close FYJC friends)

Give them **only**:

> «Use FT-E to check this question.»

Do NOT explain the interface. Observe silently. Record for each student:

| Observation | Record |
|---|---|
| Where they hesitate | timestamp + screen |
| Buttons they don't understand | widget name |
| Terminology they don't understand | exact word |
| Understands «What FT-E understood»? | yes/no/partial |
| Understands the reasoning steps? | yes/no/partial |
| Understands the C++ verification banner? | yes/no/partial |
| Understands BLOCKED / REVIEW_REQUIRED? | yes/no/partial |
| Can independently verify the answer? | yes/no |
| Can correct a misinterpreted input? | yes/no |
| Time to first understandable answer | seconds |
| Asked for human help? | where, why |

Do not change code while a student struggles — record the friction point first.
Stop only for safety or technical failure.

### Student success metrics

```
Student Independence Rate  = students completing a supported question
                             without assistance / students who attempted
Trust Rate                 = students who independently verify and accept
                             the result / students who complete the flow
Unsafe Answer Rate         = unsupported or ambiguous questions that
                             incorrectly produced a confident answer
Target:                    0% unsafe confident answers
```

### Pilot progression

1. Internal benchmark (this report) — **done**
2. Personal use
3. 3–5 close friends — Zero-Hint Protocol
4. Fix only high-impact failures (priority: Blockers A/B; anything that could
   make a student learn or submit something incorrect)
5. Repeat the benchmark — confirm no regression of 12A–14A
6. Small peer expansion (~10 students) — only if Stage 5 passes
7. Wider FYJC revision use — only after the small pilot is reliable

---

## 9. How to reproduce

```bash
python3 scripts/fte_fyjc_pilot_test.py        # Stage-1 gate (this report)
python3 scripts/fte_fyjc_student_ui_test.py   # Sprint 14 acceptance gate
python3 scripts/fte_fyjc_readiness_test.py    # Sprint 13 FYJC gate
```

Stage-1 gate summary line:
`maths 6/11 supported, refusal 9/9, bk 20/20, unsafe 2, invariants 10/10`

---

*The Stage-1 verdict is CONDITIONAL PASS. The engine refuses safely and its
book-keeping is fully correct, but the two reverse-calculation unsafe answers
(P07, P08) must be fixed before any student pilot begins — a student must never
be able to copy a wrong, confidently-presented answer.*

---

## 10. Stage 4 addendum — requested-concept routing fix (release gate reached)

Stage 4 fixed the two high-impact blockers in the question-understanding and
dependency-routing layer. C++ remains the sole mathematical authority; no
second engine, no LLM, no Python fallback was added.

### Root causes

- **Blocker A (unsafe reverse routing):** `classify_fyjc_question` returned the
  *first registered metric word anywhere in the text* — including
  `Concept: value` fact lines — instead of the concept the instruction clause
  asks for. "Find the missing figure: Expenses. Revenue: 1,000; Profit: 200"
  routed to `profit`, and the solver then *echoed the supplied Profit fact*
  (`kind: direct`, `formula_id: None`, status VERIFIED) with a C++-verified
  banner: the unsafe P07/P08 answers.
- **Blocker B (wrong dependency routing):** "Calculate ROE. Net Profit: 200; \
  Equity: 1,000" routed to `profit` (the shorter registered word inside
  "Net Profit") instead of `roe`, so it refused with the wrong missing inputs
  (Revenue/Expenses instead of Equity).

### Fixes applied

| File | Change |
|---|---|
| `backend/maths/fyjc_question.py` | Requested-concept resolution: extracts the
  object of ask-clauses ("Calculate X", "Find X", "What is X?",
  "missing figure: X", "X = ?"), resolves it against registry targets **and
  dependencies** (longest prefix, word-boundary), and flags
  `requested_uncertain` when several figures are plausible (→ REVIEW_REQUIRED,
  never guessed). Book-keeping task wording keeps priority. |
| `backend/maths/fyjc_maths.py` | **Echo gate:** a `direct` solve (requested
  concept supplied as an input) is only accepted when a registered formula
  independently derives the **same** value (formula_id set, C++ executed); a
  conflicting derivation → REVIEW_REQUIRED; no derivation → BLOCKED. It is now
  impossible to produce DERIVED/VERIFIED with `formula_id=None`. Dependency
  concepts are canonicalized to their registry spelling so reverse paths
  resolve. |
| `backend/maths/fyjc_student_flow.py` | "What FT-E understood" shows the
  canonical **Requested:** concept; uncertain requests surface as
  REVIEW_REQUIRED with a "which figure?" next action. |
| `backend/maths/student_sandbox.py` | Deterministic prose extraction
  (`extract_prose_facts`): "Revenue is Rs.10,000 and its Expenses are Rs.6,000"
  — only registered concept names immediately followed by an explicit numeric
  value (Tier 1). Closes the Stage-1 P01 prose gap with no guessing. |
| `scripts/fte_fyjc_routing_regression_test.py` | Blocker regression gate
  (44 checks). |

### Stage-4 gate result (`python3 scripts/fte_fyjc_routing_regression_test.py`)

`44/44 PASS — unsafe confident answers 0, C++ authority violations 0,
fabricated values 0, silent substitutions 0`

### Full 40-question benchmark after the fix

`python3 scripts/fte_fyjc_pilot_test.py` →

- maths supported **11/11** (was 6/11) · refusal **9/9** · book-keeping
  **20/20** · **unsafe confident answers 0** (was 2) · C++ match **11/11**
- invariants **10/10 PASS** · verdict:

> **PASS — READY FOR SMALL FYJC EXPANSION**

### Regression matrix (all green)

S15 Stage-4 gate 44/44 · S15 pilot PASS · S13 FYJC PASS · S14 acceptance PASS
· S14 UI AppTest ALL CHECKS · 12A–12F ALL CHECKS (12E 92/92, 12F PASS) ·
Formula Engine + C++ bridge ALL CHECKS · C++ `--selftest` ALL OK · Student
workspace PASS · AppTest/Demo ALL CHECKS · `py_compile` OK ·
`git diff --check` CLEAN.

### Remaining (documented, out of the routing scope)

- Trade-discount netting (B19) and ambiguous transactions (B18) stay
  REVIEW_REQUIRED by design — no registered formula nets a discount.
- Multi-document OCR conflict (P19) stays REVIEW_REQUIRED.
- Stage 1 of the pilot progression is complete and green; proceed to
  **Stage 2 (personal use)** and **Stage 3 (3–5 friends, Zero-Hint Protocol)**.

Stage-4 gate summary line:
`routing 44/44, maths 11/11 supported, refusal 9/9, bk 20/20, unsafe 0,
invariants 10/10 → PASS`
