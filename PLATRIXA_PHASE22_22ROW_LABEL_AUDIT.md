# PLATRIXA — PHASE 22: 22-ROW LABEL AUDIT

**Scope:** classification of the 22 GENUINE rows (from the reproduced
CLEAN 251 / FORMAT_ONLY 727 / GENUINE 22 classification of
`fyjc_specialist_1000.jsonl` through the unmodified production stack:
`FYJCAISpecialist.parse()` → `validate_structured_interpretation(allow_expanded=True)` →
`ExpandedGroundingGate.ground()`).

**Machine-readable evidence:** `training_data/phase22_genuine22.jsonl`
(22 records: id, input, legacy_target, specialist_output, grounding_issues).
**Audit generator:** `training/phase22_label_audit.py` (read-only; production
code untouched; deterministic via `PYTHONHASHSEED=0`).

---

## Classification summary

| Classification | Count | Meaning |
|---|---|---|
| `LABEL_QUESTIONABLE` | 5 | Legacy target claims certainty the input does not support (dual payment). 4 re-labeled correctively into v0.2; 1 documented (locked validation split). |
| `CORRECTIVE_EXAMPLE` | 17 | Legacy target defensible; the failure is the specialist runtime's. Rows (with their clean canonical labels) are the exact weak-spot training material for v0.2. |
| `TRUSTED_GOLD` | 0 | — (the 17 above also have defensible labels; they are classified corrective because they target the measured failure modes) |
| `AMBIGUOUS` | 0 | — |

All 22 rows fail the gate with the identical issue:
`Party 'Rs' not supported by input text`.

---

## Split membership of the 22 rows

| Split | Rows |
|---|---|
| `fyjc_specialist_train` (v0.1 training material) | 18 |
| `fyjc_specialist_validation` (locked) | sta_00085, con_00403, con_00438 |
| `fyjc_specialist_test` (locked) | con_00401 |

---

## The 22 rows

Legend: **fresh** = unmodified `FYJCAISpecialist.parse()` on the input.

### Pattern D — dual-payment purchases (5 × `LABEL_QUESTIONABLE`)

Legacy claims a single payment method (`cash`) + `VERIFIED` although the input
explicitly states **both cash and cheque**. The label asserts certainty the
input does not support; the fresh parse even disagrees with the legacy mode
(`cheque`, last-mentioned wins).

| # | id | split | input | legacy target | fresh output | disposition |
|---|---|---|---|---|---|---|
| 1 | sta_00085 | validation (locked) | Suresh purchased books for Rs.1000 cash. He paid Rs.500 by cheque. | purchase / cash / VERIFIED / [Suresh] / [1000, 500] | purchase / cheque / [Rs] | **documented, untouched** (v0.2 must not touch eval splits) |
| 2 | sta_00090 | train | Sanjay purchased machinery for 10k cash. She paid Rs.1000 by cheque. | purchase / cash / VERIFIED / [Sanjay] / [10000, 1000] | purchase / cheque / [Rs] | **corrective re-label** in v0.2 |
| 3 | sta_00091 | train | Raj purchased medicines for ₹4000 cash. She paid Rs. 500 by cheque. | purchase / cash / VERIFIED / [Raj] / [4000, 500] | purchase / cheque / [Rs] | **corrective re-label** in v0.2 |
| 4 | sta_00092 | train | Priya purchased furniture for ₹3000 cash. She paid Rs. 10,000 by cheque. | purchase / cash / VERIFIED / [Priya] / [3000, 10000] | purchase / cheque / [Rs] | **corrective re-label** in v0.2 |
| 5 | sta_00101 | train | Suresh purchased books for Rs.8000 cash. He paid Rs. 500 by cheque. | purchase / cash / VERIFIED / [Suresh] / [8000, 500] | purchase / cheque / [Rs] | **corrective re-label** in v0.2 |

**Corrective target rule (applied in `training/phase22_build_v02.py`):** keep
the supported purchase interpretation and both grounded amounts; record the
payment conflict honestly — `payment_method=unknown`, `payment_method_enum=UNKNOWN`,
`ambiguities=["conflicting information"]`, `ambiguity_flags=[CONFLICTING_INFORMATION]`,
`suggested_status=REVIEW_REQUIRED`, `field_confidences[payment_method].grounding=CONFLICTING`.
No accounting conclusion is invented; the runtime kernel remains the sole authority.

### Pattern A/B — short expense/payment forms with currency-token and span defects (17 × `CORRECTIVE_EXAMPLE`)

Legacy labels are defensible (party, amount, mode all supported by the text).
The fresh specialist output shows the two deterministic defects documented
below (R2-extension / R5-extension): `Rs` captured as a party, blank
transaction_type, and `X by cash` absorbed into party spans. The **canonical
train targets for these same rows are already clean** (audited: 0 `Rs`
parties, 0 blank transaction_type, 0 junk spans across all 800), so they enter
v0.2 verbatim as targeted weak-spot training material.

| # | id | split | input | legacy target (v0.2 uses this verbatim) | fresh output (defect) |
|---|---|---|---|---|---|
| 6 | con_00401 | test (locked) | paid Rs. 500 for telephone to Priya cash | expense / cash / [Priya] / [500] | tx=`''`, [Priya cash, Rs] |
| 7 | con_00403 | validation (locked) | paid Rs.12000 for groceries to Priya cash | expense / cash / [Priya] / [12000] | tx=`''`, [Priya cash, Rs] |
| 8 | con_00438 | validation (locked) | paid Rs. 500 for books to Raj cash | expense / cash / [Raj] / [500] | tx=`''`, [Raj cash, Rs] |
| 9 | con_00447 | train | paid Rs. 10,000 for fuel to Amit cash | expense / cash / [Amit] / [10000] | tx=`''`, [Amit cash, Rs] |
| 10 | con_00473 | train | paid Rs.12000 for diesel to Suresh cash | expense / cash / [Suresh] / [12000] | tx=`''`, [Suresh cash, Rs] |
| 11 | con_00504 | train | paid Rs.25000 for textiles to Rahul cash | expense / cash / [Rahul] / [25000] | tx=`''`, [Rahul cash, Rs] |
| 12 | sta_00512 | train | Ajay paid Rs.1000 cash. | **receipt** / cash / [Ajay] / [1000] | tx=`''`, [Rs] |
| 13 | sta_00516 | train | Paid Rs.25000 cash for printing charges to Deepak. | expense / cash / [Deepak] / [25000] | tx=`''`, [Deepak, Rs] |
| 14 | sta_00614 | train | Paid Rs.2000 cash for advertising to Ganesh Traders. | expense / cash / [Ganesh Traders] / [2000] | tx=`''`, [Ganesh Traders, Rs] |
| 15 | sta_00694 | train | Paid Rs.250000 to Ajay by cash. | payment / cash / [Ajay] / [250000] | tx=`''`, [Ajay by cash, Rs] |
| 16 | sta_00766 | train | Mehta paid Rs.8000 cash. | **receipt** / cash / [Mehta] / [8000] | tx=`''`, [Rs] |
| 17 | sta_00777 | train | Paid Rs. 5,000 cash for salary to Manoj. | expense / cash / [Manoj] / [5000] | tx=`''`, [Manoj, Rs] |
| 18 | sta_00834 | train | Paid Rs.2000 to Pankaj by cash. | payment / cash / [Pankaj] / [2000] | tx=`''`, [Pankaj by cash, Rs] |
| 19 | sta_00884 | train | Kumar Ltd. paid Rs. 1,000 cash. | **receipt** / cash / [Kumar Ltd.] / [1000] | tx=`''`, [Rs] |
| 20 | sta_00940 | train | Paid Rs.1000 cash for salary to Singh. | expense / cash / [Singh] / [1000] | tx=`''`, [Singh, Rs] |
| 21 | sta_00983 | train | Paid Rs.5000 cash for telephone bill to Desai. | expense / cash / [Desai] / [5000] | tx=`''`, [Desai, Rs] |
| 22 | sta_00991 | train | Paid Rs.2000 to Sanjay by cash. | payment / cash / [Sanjay] / [2000] | tx=`''`, [Sanjay by cash, Rs] |

Rows 12/16/19 note: bare `<Name> paid Rs.X cash.` is a consistent corpus
convention for money-in (`receipt`) — 13 of 14 third-person-payer rows in the
corpus are labeled `receipt` (the one exception, sta_00031, describes a
purchase narrative, not a bare payment). The legacy `receipt` labels are
therefore defensible; the fresh runtime output (`tx=''`, party `Rs`) is the
documented vocabulary gap, not a label defect.

---

## Root causes established (code-pinned, production untouched)

### 1. Numeric `.0` (FORMAT_ONLY artifacts; 727 rows; `.0` emitted on 750/1000 inputs)

`backend/maths/fyjc_ai_specialist.py:174`:

```python
value = str(round(float(raw), 2))   # "1000" -> float -> "1000.0"
```

A float round-trip on an integer-valued INR amount. The grounding gate's
digit-normalized text matching then cannot find `"1000.0"` in `"₹1,000"`
(Phase 20 finding R3). **Training-data contamination: NO** — 0/1000 legacy
targets and 0/800 canonical train targets contain `.0` amounts.

**Approved eval/training-side normalization rule (deterministic, semantic-preserving):**
strip a trailing `.0` (i.e. `^\d+\.0$` → integer digits) from amount value
strings ONLY when the value is integer-valued. Genuinely decimal amounts
(e.g. `19999.50`) are never altered. Applied implicitly in this phase's
evidence path; NOT patched into production runtime.

### 2. `Rs` extracted as a party (deterministic specialist code — NOT model/training)

`backend/maths/fyjc_ai_specialist.py:119–124` (`_PARTY_PATTERNS`):

- The `paid\s+(?:to\s+)?(...)` pattern has **no currency-token skip**, so
  `"Paid Rs.25000 …"` captures `Rs` as a party name.
- The `to\s+(...)` pattern's terminator list (`for|Rs|on|worth|,|.|$`) omits
  `by`, so `"to Pankaj by cash"` captures `Pankaj by cash`.
- `_NON_PARTY_WORDS` (line 126) does not contain `rs`.

Evidence it is code, not data: legacy `'Rs'`-party contamination = **0/1000**
(legacy) and **0/800** (canonical train); fresh specialist emits `Rs` as a
party on exactly the 22 defective inputs.

**Can training data teach the model away from it?** Partially — the v0.2
corpus supplies only clean party spans, so SFT pressure is toward clean
output. But because production inference runs *this deterministic parser* as
the interpretation path (not the model's raw span decisions), a **future
deterministic runtime patch is still REQUIRED** (boundary-aware matching,
per the Phase 21 R2 fix spec: currency tokens as terminators/non-parties,
`\s+by` as a party terminator, `rs` in `_NON_PARTY_WORDS`, word-boundary
matching). fte_fyjc_71's R2 section remains the acceptance test.

### 3. Short-sentence weakness (Pattern C coverage)

Corpus-wide coverage: `paid Rs` inputs = 22/1000; `paid Rs … for <expense>` =
16/1000; `paid Rs … to <party> by cash` = 3/1000. The v0.2 corpus includes
all surviving instances verbatim, but the volume remains thin.
**Recommendation for a future data phase (NOT generated now):** dedicated
families for (a) `Paid Rs.X to <party> by <mode>` (b) `Paid Rs.X for
<expense> to <party>` (c) `<Name> paid Rs.X cash` receipt forms — each with
the 18-field honest-label conventions used in the hardcore corpus.

### 4. Pattern D verification

The 5 dual-payment rows are the only label-integrity findings. The Phase 20
hardcore corpus contains no such rows (its multi-payment family records
`CONFLICTING_INFORMATION`/`MULTIPLE_INTERPRETATIONS` honestly).

---

## Files

- Created: `PLATRIXA_PHASE22_22ROW_LABEL_AUDIT.md` (this file),
  `training/phase22_label_audit.py`, `training_data/phase22_genuine22.jsonl`,
  `training/phase22_build_v02.py`, `training_data/phase22_v02_train.jsonl`,
  `training_data/phase22_v02_train.manifest.json`,
  `scripts/fte_fyjc_72_phase22_test.py`, `PLATRIXA_PHASE22_REPORT.md`
- Modified: none (production runtime byte-identical; frozen datasets untouched)

---

## ADDENDUM — fte_fyjc_73 sweep: Pattern D extension (4 → 21 corrective rows)

The tables above cover the 22 gate-FAILING rows only. The read-only
full-corpus sweep (`scripts/fte_fyjc_73_dual_payment_sweep.py`) later found
**17 more train-split rows with the same Pattern D label defect**, invisible to
this audit because they PASS the grounding gate — every claimed value is
individually grounded, and the contradiction lives *between* grounded values:

- 11 purchase rows: sta_00079, sta_00055, sta_00094, sta_00040, sta_00039,
  sta_00061, sta_00080, sta_00098, sta_00069, sta_00099, sta_00048
- 6 payment rows: sta_00038, sta_00041, sta_00050, sta_00054, sta_00058,
  sta_00059

On every row the fresh specialist resolves `payment_method='cheque'`
(last-mentioned-wins), contradicting the legacy single-mode `cash` +
`VERIFIED` target; the pronoun purchase rows additionally flag
`unresolved pronoun`. Scope checks: cash+credit co-occurrences are legitimate
two-transaction narratives (out of scope); the frozen hardcore corpus contains
0 `CONFLICTING_INFORMATION` rows and emits only `REVIEW_REQUIRED` (no
qualifying class); zero overlap with locked val/test; sta_00085 remains
documented-only.

Disposition: the same corrective rule, unchanged — `CORRECTIVE_TRAIN_IDS` in
`training/phase22_build_v02.py` is now the audited 21-id set; the v0.2 output
sha is `f0ba0efe9476618cc48368e57c02773cbe390f22b8e09b4d838af7e471f890d5`
(manifest updated; fte_fyjc_72 28/28 PASS). Full narrative:
`PLATRIXA_PHASE22_REPORT.md` §4.
