# Sprint 15G — Deterministic Financial Engineering Layer

**Platrixa (Platrixa)** · `docs/FYJC_15G_DETERMINISTIC_LAYER.md`

Sprint 15G hardens the existing FYJC reasoning pipeline (Sprint 15E + 15F,
which remain the untouched baseline) with HFT + Private Equity engineering
principles:

> deterministic → replayable → canonically normalized → traceable → auditable
> → discrepancy-aware

**Scope rule:** no FYJC syllabus expansion, no student-UI redesign, no
modification of the maths/routing functionality. The 15E + 15F behavior is
the baseline and is verified to remain intact (see Gates).

---

## 1. Architecture

The deterministic layer (`backend/maths/fyjc_15g.py`) sits **on top** of the
existing deterministic pipeline and never changes its output:

```
Raw Input
  → Extracted Facts                     (deterministic fact_ids, QUESTION_SUPPLIED)
  → Canonical IR                        (transaction id + account/rule/formula ids)
  → Requested Intent                    (journal / ledger / trial balance)
  → Rules / Formula IDs                 (REAL/PERSONAL/NOMINAL + registered FYJC formulas)
  → Dependencies                        (registered canonical formula ids)
  → Calculation Plan                    (BK_* steps with inputs + results)
  → C++ Authority Execution             (persistent worker == one-shot CLI, verified)
  → Verification                        (journal/ledger/TB structural checks)
  → Final Result                        (status + journal + ledger + TB)
```

Every resolved case is captured by `build_replay_record()` as a versioned
**replay record**, executed by `replay_execute()` without any natural-language
re-interpretation, and appended to an immutable **audit ledger**.

---

## 2. Replayable IR (HFT principle)

`build_replay_record(question)` returns a record with:

| Field | Meaning |
|---|---|
| `schema_version` / `reasoning_version` / `registry_version` | stable versioning (never timestamps) |
| `replay_id` | deterministic 24-hex content hash of the canonical IR + versions |
| `input` | raw wording, segments, QUESTION_SUPPLIED facts (fact_ids) |
| `canonical_ir` | transaction id, per-journal accounts, amounts, shape |
| `calculation_plan` | every BK_* step with inputs, result, provenance, canonical formula id |
| `cpp_authority` | what was sent to C++, what C++ verified (or honest `not_requested` / `engine_unavailable`) |
| `verification` | journal / ledger / trial-balance balanced flags |
| `final_result` | status, journal lines, ledger, trial balance |
| `lineage` | the machine-readable passport (section 4) |
| `discrepancies` | deterministic scan at capture time (empty ⇒ `OK`) |

**Deterministic serialization:** `serialize_replay()` uses sorted keys, stable
separators, Decimals as canonical strings, and `ensure_ascii=False` — the same
record always serializes byte-identically. No timestamps or random ids exist
anywhere in the record.

**Replay executor:** `ir_to_journal_lines(segment)` rebuilds each journal's
lines from the canonical IR (accounts + amounts + shape) with **zero NL
interpretation**, then `replay_execute()` re-posts the ledger, re-builds the
trial balance, re-verifies arithmetic, re-runs the calculation plan from its
stored inputs, and compares against the recorded outcome. Any divergence is
reported as `REPLAY_DIVERGED` — never silently corrected.

**Faithfulness gate:** for the full 15E+15F verified corpus (227 cases) the
IR → journal reconstruction is byte-identical to the pipeline output.

---

## 3. Canonical Normalization (PE principle)

`canonicalize_bk(question)` collapses equivalent textbook wording onto **one**
canonical representation:

* `canonical_transaction_id` — the registered pattern key
  (`PURCHASE_GOODS_CREDIT`, `SALE_GOODS_CASH`, …);
* `canonical_accounts` — `ACCOUNT:<chart>` / `PARTY:<name>` ids;
* `canonical_rule_ids` — `REAL` / `PERSONAL` / `NOMINAL` (registered Golden
  Rules);
* `canonical_formula_ids` — registered FYJC relationships behind the amount
  pipeline (`TRADE_DISCOUNT`, `NET_PRICE`, `CREDITOR_BALANCE`, …).

Examples that converge:

```
Bought furniture for cash ₹15,000.
Purchased furniture and paid cash ₹15,000.
Furniture purchased against cash ₹15,000.
→ one canonical IR (and one replay_id)
```

```
Bought goods on credit from Rahul ₹22,000.   ==   Bought goods on account from Rahul ₹22,000.
```

Insufficient confidence (unrecognised wording, ambiguous cash/credit, a party
placeholder that resolved to nothing) ⇒ **REVIEW_REQUIRED** — Platrixa never
guesses a canonical concept. `canonical_equivalent(a, b)` asserts convergence.

A small pipeline-consistency fix was required here (15G-required, verified
non-regressing): `resolve_transaction_amounts` now treats `on account` as
credit mode exactly like `classify_bk_type` already did, so the recorded IR
of the two equivalent wordings converges. Journal lines are unchanged (15E and
15F gates stay 30/30 and 43/43).

---

## 4. Lineage Passport

Every confident output carries `build_lineage(...)`, which answers the eight
lineage questions:

1. **What did Platrixa receive?** — `received.raw_input` + segments.
2. **What did Platrixa understand?** — `understood.pattern_key/label/requested_operation`.
3. **Which canonical concepts were selected?** — `canonical` (ids + accounts).
4. **Which rule/formula was used?** — `rules_used` + `formulas_used`.
5. **Which values were supplied vs calculated?** — `values[]` each tagged
   `QUESTION_SUPPLIED` or `CALCULATED`; a supplied fact never appears as a
   calculated value (`supplied_vs_calculated_overlap` is always `[]`).
6. **What was sent to C++?** — `cpp.sent` (registered metrics only).
7. **What did C++ verify?** — `cpp.verified` + `cpp_authority.outcomes`.
8. **Why VERIFIED/DERIVED?** — `output.why_final` (deterministic composition).

---

## 5. Immutable Audit Record

`AuditLedger` / `append_audit_record()` / `audit_snapshot()` provide an
append-only, versioned trail:

* records are **deep-copied on append and on snapshot** — no caller can mutate
  a historical record;
* a registry/rule/reasoning version change produces a **new** versioned record
  (the `replay_id` includes the versions);
* entries carry `audit_sequence` (deterministic increment — no timestamps),
  versions, `replay_id`, execution/verification status, authority state,
  lineage and discrepancy count;
* **no secrets, no unnecessary personal/student information** are stored.

---

## 6. Discrepancy Detection

`validate_journal` / `validate_ledger` / `validate_trial_balance` /
`validate_pipeline` are deterministic structural checks:

| Check | Invariant |
|---|---|
| Journal | Total Debit == Total Credit; debit/credit lines present; no duplicate line; no account on both sides; no invented account |
| Ledger | per account `Opening(0) + Debit − Credit == Closing`; total Dr == total Cr |
| Trial balance | Total Debit == Total Credit; no negative / dual-side rows |
| Pipeline | every VERIFIED journal is balanced and carries calculation provenance; refusals carry zero lines |

A discrepancy is **never silently repaired**: the validators return an
explicit state (`OK` / `REVIEW_REQUIRED`) with machine-readable codes
(`JOURNAL_UNBALANCED`, `MISSING_CREDIT_LINE`, `DUPLICATE_LINE`,
`INVENTED_ACCOUNT`, `LEDGER_ACCOUNT_INCONSISTENT`, `TB_UNBALANCED`,
`REPLAY_DIVERGED`, `FORMULA_ID_NONE_CONFIDENT`, `CPP_RESULT_MISMATCH`,
`UNSUPPORTED_DEPENDENCY`, …). Tampered fixtures are detected by the 15G gate.

---

## 7. C++ Authority Optimization (HFT-inspired)

The compiled C++ engine (`formula_engine/formula_engine.cpp`) gained a
**persistent `--worker` mode**: one long-lived process, one JSON document per
line in, one result per line out, executing the *exact same* `run_cli()` path
as the one-shot CLI. This removes the per-request process-spawn and
JSON-bootstrap overhead while keeping every result **byte-identical**.

`CppAuthorityWorker` (in `fyjc_15g.py`) is the Python-side persistent
transport with a structural equivalence guarantee: on any I/O failure it
tears down and the caller falls back to the one-shot path — correctness,
determinism, lineage, refusal safety and the C++ authority rules are never
weakened. `cpp_authority_execute()` runs the worker **and** the one-shot CLI
and asserts equality.

Python remains responsible for semantic understanding/orchestration; C++ is
the sole mathematical authority.

### Measured benchmark (this machine, medians)

| Measurement | Before (one-shot) | After (worker) | Speed-up |
|---|---|---|---|
| IR → C++ execution | 2.56 ms | 0.06 ms | **46.2×** |
| Formula/rule lookup | 2.54 ms | 0.05 ms | **56.1×** |
| Journal validation | — | 0.71 ms across 146 journals (≈0.005 ms each) | — |
| Ledger validation | — | 0.035 ms | — |
| Trial-balance validation | — | 0.026 ms | — |
| Complete authority execution (build + replay + C++ verify) | — | 17.1 ms | — |

Equivalence gate: **worker == one-shot for every payload** (no mismatches).

---

## 8. Determinism Contract

For identical input IR + registry version + engine version + execution
configuration, output is identical. Verified by the 15G gate:

* repeated in-process execution;
* replay execution (`replay_execute` twice);
* serialize → deserialize → execute;
* multiple executions in the same process;
* separate-process execution (two fresh interpreter runs produce byte-identical
  serialized records).

No hidden randomness anywhere in the layer.

---

## 9. Sprint 15G Gates

`scripts/fte_fyjc_15g_test.py` — **90/90 checks passed**, including:

* replay corpus: 150 (126 verified + 24 refusals) + 101 15E-verified cases
  re-execute byte-identically;
* canonical convergence families + `REVIEW_REQUIRED` on ambiguity;
* lineage 100% on confident outputs; 0 supplied-as-calculated overlaps;
* audit immutability (append-only, snapshot-mutation-safe, version bumps);
* discrepancy detection on tampered fixtures (never silently repaired);
* C++ worker == one-shot; registered metrics always carry `formula_id`;
* hard release gates all zero: fabricated values, invented accounts, silent
  substitutions, unsafe confident answers, unbalanced VERIFIED journals,
  `formula_id=None` confident results, C++ authority violations, unvalidated
  derivations; 100% deterministic replay; 100% lineage; discrepancy cases
  detected; canonical equivalents converge.

`scripts/fte_fyjc_15g_cpp_benchmark.py` — before/after authority benchmark
with the worker/one-shot equivalence gate.

## 10. Regression Compatibility

* Sprint 15E gate: **30/30 PASS** (unchanged).
* Sprint 15F gate: **43/43 PASS** (unchanged).
* C++ engine self-test: **ALL OK**.
* The only 15E/15F-touching change is the `on account` credit-mode
  consistency fix (section 3), which cannot alter journal lines and keeps
  both gates green.

## 11. Deliverables

| File | Purpose |
|---|---|
| `backend/maths/fyjc_15g.py` | replay IR + executor, canonical normalization, lineage, audit ledger, discrepancy validator, C++ worker |
| `scripts/fte_fyjc_15g_test.py` | Sprint 15G gate (90 checks) |
| `scripts/fte_fyjc_15g_cpp_benchmark.py` | before/after C++ authority benchmark |
| `formula_engine/formula_engine.cpp` | additive `--worker` persistent mode (default CLI unchanged) |
| `docs/FYJC_15G_DETERMINISTIC_LAYER.md` | this document |
| `backend/maths/fyjc_bk_reasoning.py` | one-line amount-pipeline consistency fix (`on account` == `on credit`), required by canonical equivalence |

No commit/push is performed in this sprint; the layer stops after
verification.
