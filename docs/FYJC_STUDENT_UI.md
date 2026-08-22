# Sprint 14 — FYJC Student End-to-End UI Integration

**Scope:** turn Sprint 13's FYJC backend capability (Maths + Book-Keeping &
Accountancy readiness) into an actual student-usable product inside the
Streamlit workspace.

**Sprint 13 engines are reused untouched.** This sprint adds:

| File | Role |
|---|---|
| `backend/maths/fyjc_student_flow.py` | Pure, deterministic journey orchestration (no Streamlit, no AI, no network) |
| `backend/fyjc_student_ui.py` | Streamlit rendering of the FYJC Study / Verify page |
| `scripts/fte_fyjc_student_ui_test.py` | Sprint 14 acceptance gate (deterministic, headless) |
| `scripts/fte_fyjc_student_ui_apptest.py` | UI smoke test (AppTest renders the real page) |
| `app (1) (9).py` | adds the **FYJC Study** page to the workspace navigation (real + demo) |
| `docs/FYJC_STUDENT_UI.md` | this document |

## 1. The student journey

```
📸 Photo / 📁 PDF / ✍️ Type
   -> What Platrixa understood   (editable: ✏️ Correct / Edit)
   -> Maths | Book-Keeping flow (steps 1-6 / 1-8)
   -> C++ mathematical authority confirmation  (⚙️ Deterministic calculation verified)
   -> expandable technical audit (optional)
   -> Independent verification (student's own answer / journal / ledger / trial balance)
```

### Maths flow (steps 1–6)
1. **Given** — the extracted inputs (each with its provenance).
2. **Required** — the metric being asked for.
3. **Formula** — the registered formula + formula ID (from the 12A–12F registry).
4. **Substitution** — the actual values inserted into the formula.
5. **C++ Calculation** — *"⚙️ Deterministic calculation verified — the
   arithmetic was executed by the C++ mathematical authority."* The audit
   expander shows formula ID, inputs, result, status and authority.
6. **Final Answer** — the display value with % / currency as applicable.

### Book-Keeping flow (steps 1–8)
1. **Identify Accounts** — e.g. Purchases, Rahul.
2. **Classify Accounts** — Sprint 13 modern-approach role plus the FYJC
   traditional class (Personal / Real / Nominal) as a presentation mapping.
3. **Apply the Golden Rule** — the exact rule text.
4. **Debit / Credit Decision** — *why* each account is debited/credited.
5. **Journal Entry** — the entry lines.
6. **Ledger Effect** — each account's Dr/Cr totals and balance.
7. **Trial Balance Effect** — totals and TALLIES / DOES NOT TALLY.
8. **Verification** — Debit total = Credit total.

### Refusal states
- 🔴 **BLOCKED** — what is missing, why it is required, and concrete next
  steps; for Maths the page offers **enter the missing value manually**
  (labelled student-entered, never document data).
- 🟠 **REVIEW REQUIRED** — what appears inconsistent/ambiguous and why Platrixa
  will not silently choose (e.g. cash-vs-credit purchases, discount % that
  no registered formula can net, conflicting sources).
- 🟡 **NOT SUPPORTED YET** — no answer generated, with the supported-topic
  list shown for guidance.

### Independent verification (mandatory)
- Maths: enter your answer → ✅ MATCH / 🔴 MISMATCH with the difference and
  the registered formula to re-check against.
- Accounting: verify a journal entry (balance + golden-rule direction),
  a ledger balance (amount + Dr/Cr side), a trial balance
  (`Account, Dr, Cr` lines), and a built-in arithmetic consistency check.

## 2. Input honesty (photos)

**No OCR engine is bundled in this deployment.** A photo is shown to the
student and clearly labelled as *not machine-read*; Platrixa never pretends it
read the photo and never guesses its text. The student types/pastes the
question (the photo stays visible as source evidence). A scanned
photo-PDF with no extractable text is reported honestly the same way.
This is deliberate: correctness is never weakened to make extraction
"succeed" — bad extraction → REVIEW/BLOCKED.

## 3. Architecture guarantees (unchanged from 12A–13)

- **C++ is the sole mathematical authority.** Every resolved Maths number
  flows through `verify_maths_answer → solve_strict → C++`. Python never
  performs a fallback calculation.
- **Accounting treatment** comes from the Sprint 13 golden-rule layer;
  ledger/trial-balance arithmetic shown is verification arithmetic over
  the student's own postings.
- **No fabricated values, no silent substitution, no open-web fallback.**
  BLOCKED / REVIEW_REQUIRED / UNSUPPORTED are valid outcomes.
- **Deterministic:** identical input ⇒ identical output (tested).

## 4. Release gates (Sprint 14 §12)

- Sprint 14 gate: `python3 scripts/fte_fyjc_student_ui_test.py` — 100/100
- UI smoke: `python3 scripts/fte_fyjc_student_ui_apptest.py`
- Sprint 13 FYJC gate, 12A (202), 12B (123), 12C (99), 12D (68),
  12E (92), 12F, Formula Engine (Python + C++ bridge), C++ `--selftest`,
  student workspace (98/98), demo/app suites — all green.
- `git diff --check` clean.

## 5. Student usability record sheet (Sprint 14 §11.F)

For a real student trial, run at least 3–5 questions (use the golden
dataset in `backend/maths/fyjc_dataset.py` — e.g. **S01**, **S02**,
**M03**, **A03**, **M12**) and record per question:

| # | Question | Hesitation point | Misunderstood what? | Asked for help with | Found the final answer? | Verified independently? |
|---|---|---|---|---|---|---|
| 1 | | | | | ✅/❌ | ✅/❌ |

Rules: **do not change code while the student struggles — first record
the friction point.** Known friction candidates to watch for:

- Narrative maths text without `Concept: value` lines parses no facts →
  BLOCKED with guidance (by design; the entry placeholder and BLOCKED
  copy teach the format).
- Transactions with a discount % → REVIEW with the "no registered formula
  for netting" note (enter the net amount).
- `Purchased goods for …` without cash/credit → REVIEW until the student
  adds the wording.
