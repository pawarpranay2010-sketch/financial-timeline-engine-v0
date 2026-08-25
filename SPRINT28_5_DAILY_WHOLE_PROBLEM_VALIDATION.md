# Sprint 28.5 — Automated Daily Whole-Problem Student Validation

**Classification:** VALIDATOR DELIVERED — 1 NEW PRODUCTION DEFECT DETECTED (report-only, not fixed)

**Production code modified: ZERO LOC.**

---

## 1. Deliverables

| File | Purpose |
|------|---------|
| `scripts/fte_daily_whole_problem_validation.py` | One-command daily validator (whole problems, safety, determinism, baseline regression diff) |
| `scripts/fte_daily_whole_problem_baseline.json` | Known-good baseline digests + summaries for regression comparison |

Run daily with:

```
python3 scripts/fte_daily_whole_problem_validation.py            # validate vs baseline
python3 scripts/fte_daily_whole_problem_validation.py --update-baseline   # after triage
```

Non-zero exit status on any critical failure (`incorrect_verified > 0`, safety
violation, determinism failure, ledger reconciliation failure,
unexpected REVIEW_REQUIRED, or new baseline regression flag).

---

## 2. Daily Corpus (4 complete whole problems)

Privacy-cleaned (neutral party names; no contact/college identifiers), but
structurally faithful to real student input.

| ID | Coverage | Expected |
|----|----------|----------|
| DWP001_FULL_CYCLE | Opening capital → credit purchase → credit sale → expense → cash receipt from debtor → part payment to creditor | 6/6 VERIFIED, exact ground-truth ledger |
| DWP002_GST_CREDIT_CYCLE | GST cash purchase → GST credit sale → full receipt by cheque | 4/4 VERIFIED, ground-truth ledger incl. CGST/SGST |
| DWP003_STUDENT_TYPO_INPUT | Lowercase, spelling slips ("bussiness", "recieved"), multi-line input | Capability boundary (no full-verify claim) |
| DWP004_DISCOUNT_SETTLEMENT | Trade discount purchase + full settlement (splitter merge case) | EXPECTED_REVIEW_REQUIRED |

---

## 3. Today's Validation Result

```
Problems tested: 4          Whole problems VERIFIED: 2
Incorrect VERIFIED: 2       Safety violations: 0
Determinism failures: 0     Ledger reconciliation failures: 0
RESULT: FAIL (critical regression detected)
```

The FAIL is **correct and intended**: the validator caught a genuine
production defect on its first run (Section 5).

### Per-problem

| Problem | Result | Notes |
|---------|--------|-------|
| DWP001 | PROBLEM_VERIFIED ✅ | 6/6 VERIFIED; ledger matches ground truth exactly; deterministic |
| DWP002 | PROBLEM_VERIFIED ⚠ | **INCORRECT_VERIFIED detected** — see Section 5 |
| DWP003 | PROBLEM_NOT_SUPPORTED ✅ | Safe refusal of typo'd opening entry and malformed receipt; splitter merges lines 2–4 (known limitation); no unsafe claim |
| DWP004 | PROBLEM_REVIEW_REQUIRED ✅ | EXPECTED_REVIEW_REQUIRED (Sprint 23 Category-C splitter limitation, deferred by spec) |

All four problems: byte-identical across 3 repeated runs. Ledger recomputed
independently from state deltas matches the engine snapshot in every problem.

---

## 4. Safety Invariants (all zero ✅)

unsafe_confident = 0 · invented_accounts = 0 · invented_amounts = 0 ·
unbalanced_verified = 0 · state_leaks = 0 · double_mutations = 0 ·
ledger_reconciliation_failures = 0 · determinism_failure = 0
(`incorrect_verified` = 2, detailed below — the validator's reason to fail)

---

## 5. NEW PRODUCTION DEFECT DETECTED (not fixed, per spec §11)

**PRODUCTION CHANGE REQUIRED**

- **Observed failure:** `"Received Rs.<amount> from <party> by cheque"`
  produces `Dr <party> / Cr Bank` — inverted.
  Correct treatment: `Dr Bank` (bank is the receiver) `/ Cr <party>` (the
  debtor is the giver). The party's receivable is *increased* instead of
  settled, and Bank is credited as if it paid money out.
  Example (DWP002 T4): engine posts Dr Suresh 23,600 / Cr Bank 23,600;
  correct is Dr Bank 23,600 / Cr Suresh 23,600.
- **Scope:** only when the amount precedes "by cheque"
  (`"Received Rs.X from Y by cheque"`). The variants
  `"Received cheque of Rs.X from Y"` and `"Y paid Rs.X by cheque"` post correctly,
  as does `"Received Rs.X cash from Y"`.
- **Existing component responsible:** receipt/instrument reasoning path in
  `backend/maths/fyjc_bk_reasoning.py` (receiver/giver classification for
  "received … by cheque" phrasing).
- **Why existing behavior is insufficient:** it classifies the paying party as
  the receiver whenever the amount token precedes "by cheque".
- **Smallest possible fix:** align that one phrasing branch with the already-correct
  `"cheque of"`/party-paid branches (Dr Bank, Cr party).
- **Estimated LOC:** ~5–10.
- **Regression risk:** low — narrow phrase pattern; DWP001/DWP002 in this corpus
  become permanent regression cases once fixed.
- **Second, related exposure (DWP003):** the splitter merged three student lines
  into one segment and posted a *balanced-but-wrong* VERIFIED journal
  (Dr Purchases 12,000 / Cr Bank 8,000 / Cr Ramesh 4,000 — Mehta vanishes).
  Balanced checks pass; account-level ground truth fails. This is the known
  Sprint 23 splitter limitation now demonstrably producing an incorrect VERIFIED
  compound entry — recorded for the future dedicated splitter sprint.

Per Sprint 28.5 §11, no production change was made automatically.

---

## 6. Regression Gates

| Gate | Result |
|------|:------:|
| Sprint 16 Problem Engine | PASS |
| Sprint 17 Workflow | PASS |
| Sprint 18 Whole-Problem (+ Projection Parity 279/279) | PASS |
| Sprint 19 Capability Corpus | RELEASE READY (determinism 10/10, ledger integrity clean) |
| Sprint 27 Mutation Safety | 15/15 |
| Settlement Blocker Regression | ALL PASS |
| Boundary Closure | 852/852 |
| Chaos Full Audit (`_15i_chaos_full_audit_28.py`) | CLEAN — no new production bugs |
| Production Capability 35 | All invariants zero |
| py_compile / git diff --check | PASS |

**Documented stale expectations (pre-existing, NOT modified):**
`fte_fyjc_15k_gst_test.py` F.6 expects REVIEW_REQUIRED for
"inclusive of GST @ 18%" — superseded by the approved Sprint 24 change (explicit
rate + inclusive marker is deterministic; VERIFIED is the correct treatment).
The older `fte_fyjc_15chaos_*` variant scripts show failures that exist against
committed HEAD (production untouched since `6ea3f6a`) and encode pre-Sprint-24
expectations plus deferred splitter limitations. These were not part of the
gate set used by Sprints 23–27 reports; left untouched per spec.

---

## 7. Baseline Regression Mechanism

Verified working:
- First run created `fte_daily_whole_problem_baseline.json` (per-problem SHA-256 digest + summary).
- Simulated stale-baseline run correctly emitted `OUTPUT_CHANGED` flag.
- `--update-baseline` re-persists current results after triage.

Growth policy (per spec §14): real student problem → privacy cleanup → add to
CORPUS list → `--update-baseline` → permanent daily regression case.

---

## 8. Recommendation

1. Fix the receipt-by-cheque direction defect (smallest fix, ~5–10 LOC in
   `fyjc_bk_reasoning.py`), then DWP002 flips the daily run to PASS.
2. Schedule the deferred splitter-corpus sprint — DWP003 shows merged segments
   can produce balanced-but-wrong VERIFIED journals, which balances-only
   checks cannot catch.
3. Run `python3 scripts/fte_daily_whole_problem_validation.py` before every release.
