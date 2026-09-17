# PLATRIXA — PHASE 22 REPORT (v0.2 TRAINING PREPARATION)

Status: **PASS — READY FOR v0.2 LoRA TRAINING**

This report closes Phase 22. It consolidates the controlled data-quality and
training-preparation audit (22-row label audit), the v0.2 training corpus
build, and the fte_fyjc_73 addendum sweep that extended the audited Pattern D
corrective set from 4 to 21 rows.

Hard safety rules held for the entire phase:

- production runtime files: **byte-identical to git HEAD** (verified by
  fte_fyjc_72 check B/C on every run)
- frozen Phase 20/21 dataset: **byte-identical**
  (sha256 `56be5be11e771c8af553bc58dec2746a871521c11c7f9c07aec3eb01bcc721cd`)
- original 1,000-row specialist corpus: **byte-identical to git HEAD**
- locked validation/test splits: **untouched** (sta_00085 documented, never relabeled)
- v0.1 adapter/model: untouched
- nothing committed, nothing pushed (repository operations remain with the user)

---

## 1. STEP 1 — where Phase 22 preprocessing lives

Production stays untouched by design: the specialist, schema verifier, and
grounding gate are runtime code, and a training-data phase must never patch
them. All Phase 22 logic therefore lives on the training side:

- `training/phase22_label_audit.py` — read-only reproduction of the
  251/727/22 classification through the unmodified production stack
  (`FYJCAISpecialist.parse()` → `validate_structured_interpretation(
  allow_expanded=True)` → `ExpandedGroundingGate.ground()`), writing
  `training_data/phase22_genuine22.jsonl`.
- `training/phase22_build_v02.py` — v0.2 corpus builder (compose → validate →
  dedup → leakage gates → deterministic write), writing
  `training_data/phase22_v02_train.jsonl` + manifest.
- `scripts/fte_fyjc_72_phase22_test.py` — 28-check acceptance suite (A–J).
- `scripts/fte_fyjc_73_dual_payment_sweep.py` — read-only full-corpus sweep
  that enumerates every remaining dual-mode VERIFIED target and captures fresh
  specialist evidence.

Determinism: both training scripts re-exec with `PYTHONHASHSEED=0`
(Phase 21 finding R1: the specialist iterates keyword sets), fixed corpus
ordering, no timestamps.

## 2. STEP 2–4 — 22-row label audit (reproduced exactly)

Classification through the current production stack: **CLEAN 251 /
FORMAT_ONLY 727 / GENUINE 22** (schema PASS 1000/1000, exceptions 0).
Full evidence and row tables: `PLATRIXA_PHASE22_22ROW_LABEL_AUDIT.md`.

Root causes established, code-pinned:

1. Numeric `.0` — `backend/maths/fyjc_ai_specialist.py:174`
   (`str(round(float(raw), 2))`); 727 rows FORMAT_ONLY; **no training-data
   contamination** (0 `.0` amounts in legacy or v0.2 targets).
2. `Rs` captured as a party + `X by cash` junk spans —
   `fyjc_ai_specialist.py:119–126` (`_PARTY_PATTERNS` lacks currency-token
   skips and the `by` terminator; `_NON_PARTY_WORDS` lacks `rs`).
   Deterministic parser defects, not model failures.
3. Short-sentence coverage thin: `paid Rs` forms = 22/1000.
4. Pattern D (dual payment) — 5 rows, one locked (sta_00085), 4 corrective.

`Rs`-party contamination in training labels: 0/1000 legacy, 0/800 canonical
train, 0/998 hardcore — the runtime defect is not in the training data.

## 3. v0.2 corpus (phase22_v02_train.jsonl)

1,793 rows = 779 canonical kept + 21 corrective re-labels + 998 hardcore
verbatim (hc_00092/hc_00928 excluded per Phase 21; 0 gate/schema drops;
5 leakage/duplicate drops per manifest).

- output sha256: `f0ba0efe9476618cc48368e57c02773cbe390f22b8e09b4d838af7e471f890d5`
- every row passes the production schema verifier (18-field contract)
- leakage checks ALL PASS: 0 overlap with canonical val/test, 0 exact and 0
  near-dup (Jaccard ≥ 0.75) vs the Phase 17 locked benchmark, unique ids and
  inputs, excluded ids absent
- distributions in `training_data/phase22_v02_train.manifest.json`
  (suggested_status: 1,167 REVIEW_REQUIRED / 626 VERIFIED)

Target format: `{id, input, output, metadata}` rows; assistant target = compact
18-field JSON (v0.1 job contract) — compatible with `training/trl_sft_job.py`.

## 4. fte_fyjc_73 addendum — Pattern D extension (4 → 21 corrective rows)

fte_fyjc_72 check G flagged 11 purchase rows outside the audited set. The
read-only sweep (fte_fyjc_73) then found **17 total uncorrected dual-mode
rows** — the same Pattern D defect class, invisible to the Phase 22 audit
because these rows PASS the grounding gate (every claimed value is
individually grounded; the label defect is a contradiction *between* grounded
values).

- 11 purchase rows: sta_00079, sta_00055, sta_00094, sta_00040, sta_00039,
  sta_00061, sta_00080, sta_00098, sta_00069, sta_00099, sta_00048
  ("X purchased/bought … for ₹N cash. He/She paid ₹M by cheque.")
- 6 payment rows: sta_00038, sta_00041, sta_00050, sta_00054, sta_00058,
  sta_00059 ("Paid <P1> ₹N cash and received ₹M from <P2> by cheque.")

Objective evidence on every row (unmodified production
`FYJCAISpecialist.parse()`): the fresh parse resolves `payment_method='cheque'`
(last-mentioned-wins), directly contradicting the legacy single-mode `cash` +
`VERIFIED` claim; on the pronoun purchase rows it additionally flags
`unresolved pronoun`.

Scope boundary, verified not assumed:

- cash+credit co-occurrences (e.g. sta_00049/37/51/34) are legitimate
  two-transaction narratives — `cash` grounds to clause 1, `credit` belongs to
  a separate transaction; **out of scope**, labels stand.
- frozen hardcore corpus contains **0** `CONFLICTING_INFORMATION` rows and
  emits only `REVIEW_REQUIRED` — no false-certainty class exists there, so no
  hardcore row qualifies (and hardcore is untouchable regardless).
- all 17 rows are train-split only; zero overlap with locked val/test.
- sta_00085 (validation split) remains documented-only, consistent with the
  original audit.

Disposition: the already-established corrective rule applies unchanged
(transaction interpretation and both grounded amounts kept; payment conflict
recorded honestly: `payment_method=unknown`, `payment_method_enum=UNKNOWN`,
`ambiguities=["conflicting information"]`, `ambiguity_flags=
[CONFLICTING_INFORMATION]`, `suggested_status=REVIEW_REQUIRED`, payment_method
field confidence grounded as CONFLICTING). No new semantics were invented; no
accounting conclusion is drawn — the kernel remains the sole authority.

Also fixed during this addendum: the builder's duplicated
`CORRECTIVE_TRAIN_IDS` definition (the second silently shadowed the first and
listed locked-split row sta_00085 — had it been reached it would have
SystemExit'd; consolidated into one audited 21-id set).

## 5. Verification (fte_fyjc_72: 28/28 PASS)

| Area | Result |
|---|---|
| A. frozen artifacts unchanged | hardcore sha == 56be5be1…, 1000 rows, original corpus unchanged vs HEAD, manifest sha matches |
| B/C. production runtime untouched | zero tracked modifications |
| D. classification reproduction | CLEAN 251 / FORMAT_ONLY 727 / GENUINE 22; 22 genuine records; Rs-party issue on all |
| E. v0.2 integrity | 1,793 rows, 18-field contract, production schema verifier passes on all, unique ids/inputs |
| F. leakage | 0 val/test overlap, 0 exact/near-dup vs Phase 17 benchmark, hc_00092/hc_00928 absent |
| G. corrective re-labels | exactly 21, all record CONFLICTING_INFORMATION/UNKNOWN/REVIEW_REQUIRED; no uncorrected dual-mode VERIFIED purchase or payment target remains |
| H. canonical fidelity | kept rows byte-identical to v0.1 train source |
| I. manifest consistency | row_count, output sha, leakage PASS all match disk |
| J. determinism | builder rerun byte-identical |

fte_fyjc_73 post-fix: 0 remaining dual-mode VERIFIED rows (was 17).

## 6. What this means for v0.2 training

Ready: `training_data/phase22_v02_train.jsonl` is the training slice for
`platrixa-fyjc-specialist-v0.2` under the v0.1 job contract. The 21 corrective
rows teach the model the honest dual-payment convention; the canonical +
hardcore rows provide clean party spans and integer-string amounts.

Not done here (by scope rules): no runtime patch for the deterministic
defects — the `Rs`/`by cash` party-span fix remains a FUTURE PATCH with the
Phase 21 R2 spec as its acceptance test; the numeric `.0` emission likewise
stays runtime-side. Training data cannot fix deterministic parser code;
inference runs the parser.

Recommended next data phase (NOT executed): dedicated families for
`Paid Rs.X to <party> by <mode>`, `Paid Rs.X for <expense> to <party>`, and
`<Name> paid Rs.X cash` receipt forms (22/1000 coverage is thin).

## 7. Artifacts

Created/updated in Phase 22 (all untracked; commit/push deliberately not
performed):

- `PLATRIXA_PHASE22_22ROW_LABEL_AUDIT.md`, `PLATRIXA_PHASE22_REPORT.md` (this file)
- `training/phase22_label_audit.py`, `training/phase22_build_v02.py`
- `training_data/phase22_genuine22.jsonl`, `training_data/phase22_v02_train.jsonl`,
  `training_data/phase22_v02_train.manifest.json`
- `scripts/fte_fyjc_72_phase22_test.py` (28 checks),
  `scripts/fte_fyjc_73_dual_payment_sweep.py` (sweep tool)

Modified production files: **none**. Frozen artifacts: **none**.
