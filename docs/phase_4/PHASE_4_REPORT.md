# PHASE 4 REPORT — Invoice Integration / Authority Boundary Validation

Sprint INV-ROLE · 2026-09-25 · Objective: **prove** that the Phase 3
invoice layer integrates safely with the existing Platrixa architecture
and existing deterministic authorities. This is a validation phase, not
a feature-expansion phase.

Thesis held end-to-end: *AI interprets; Platrixa deterministically
validates, grounds, routes, and executes only what its authorities can
prove.* Recognition ≠ Authority.

---

## 1. Executive summary

- Phase 3 checkpoint is clean and committed (`79edb78`, pushed to `main`).
- The 30/30 REVIEW_REQUIRED benchmark behavior is **fully explained** by
  the pre-existing, suite-pinned normalization party rule
  (`_SINGLE_LETTER_RE`), not by any invoice defect. Conclusion **B + C**;
  the rule was **not** weakened and no bypass was created
  (`docs/phase_4/party_boundary_audit.json`).
- The 30-fixture integration matrix with per-stage evidence passes
  **249/249** checks; the false-VERIFIED attack suite passes **50/50**
  with `false_VERIFIED == 0`; the party boundary gate passes **27/27**.
- Two genuine integration defects were found and minimally fixed:
  1. **Case I (invented account):** the invoice executor defaulted
     `Purchases` for **service** wording that the classification
     machinery itself refuses (`NOT_SUPPORTED`). Now fails closed
     (`invoice_executor.py`, +31 guarded lines).
  2. **Registry data defect:** `limitations=( "...string..." )` without a
     trailing comma iterated **character by character**, silently
     destroying refusal evidence on 9 capabilities. `register()` now
     rejects bare strings; all data fixed (`capability_registry.py`,
     +29/−10).
- Full regression: every previously passing suite still passes; the two
  known failures (GST F.6/H.1, DU3) are **pre-existing** and unchanged.
- Real E2E run once: 0 VERIFIED / 30 REVIEW_REQUIRED / 0 UNSUPPORTED;
  `false_VERIFIED = 0`. Gold labels (27×VERIFIED) predate the party-ID
  reality and are documented as inconsistent with pinned safety rules —
  the kernel was NOT weakened to satisfy stale gold.

## 2. Files changed (Phase 4)

| File | Change | Why |
|---|---|---|
| `backend/maths/invoice_executor.py` | +31 | Case-I fix: service wording without a supported account refuses instead of inventing `Purchases`/`Sales` |
| `backend/maths/capability_registry.py` | +29/−10 | bare-string `limitations` rejected at registration; 9 data fixes |
| `scripts/fte_invoice_party_boundary_test.py` | NEW (27 checks) | Phase 2 deliverable |
| `scripts/fte_invoice_integration_gate_test.py` | NEW (249 checks) | Phase 3+6 deliverable |
| `scripts/fte_invoice_false_verified_gate_test.py` | NEW (50 checks) | Phase 5 deliverable |
| `docs/phase_4/party_boundary_audit.json` | NEW | Phase 2 audit |
| `docs/phase_4/invoice_fixture_matrix.json` | NEW | 30 live stage traces |
| `docs/phase_4/authority_boundary_matrix.json` | NEW | live registry export + observed routing |
| `docs/phase_4/false_verified_matrix.json` | NEW | live attack-case results |
| `docs/phase_4/PHASE_4_REPORT.md` | NEW | this report |

No other production file was modified. Phase 1–3 frozen artifacts
(`benchmark/real_invoice_e2e/*`) are byte-identical to commit `79edb78`.

## 3. Diff/stat (Phase 4 production changes)

```
 backend/maths/capability_registry.py | 29 +++++++++++++++++++----------
 backend/maths/invoice_executor.py    | 31 +++++++++++++++++++++++++++++++
 2 files changed, 50 insertions(+), 10 deletions(-)
```

Plus 3 new gates (1,186 test lines) and 5 new docs/phase_4 artifacts.

## 4. Phase-3 baseline (checkpoint, before any Phase 4 change)

| Suite | Result | vs Phase 3 recorded |
|---|---|---|
| P0 | 138/138 PASS | = |
| Batch-1 | PASS (59/59) | = |
| Boundary closure | 852/852 PASS | = |
| GST 15K | 86 pass / 2 fail (F.6, H.1) | = pre-existing |
| Core maths | 202/202 PASS | = |
| Registry C+E | 220/220 PASS | = |
| Doc-understanding P1/P2 | 63/63, 46/46 PASS | = |
| Doc-understanding P3 | 50/51 (1 fail) | = pre-existing |
| Student production gate | PASS | = |
| Invoice-role gate | 79/79 PASS | = |

## 5. Party-ID investigation (Phase 2)

Probe matrix through the REAL path (`From: <value>` on a reconciled
invoice): `B-001-TEST-BUYER` → REVIEW_REQUIRED (concern), `B-001` →
same, `BUYER` / `ABC LTD` / `ABC PVT LTD` / `A` / `A BUYER` / `Anil` /
`Ram & Sons` → VERIFIED via `invoice_labelled_facts`.

Layer localization: **normalization** raises the concern
(`fyjc_normalization.py` `_SINGLE_LETTER_RE` sweep, line ~320); the
role layer is clean; the executor (called ungated) composes; no
authority is involved; the orchestrator hands the document to the
narration path verbatim (no invoice evidence in the result).

Conclusion: **B — existing party validation is intentionally correct**
(pinned by 15I-VY / 15I-COVER / bills-authority suites predating
Phase 3), **C — not required** (real document party names compose
already). D rejected: loosening letter-adjacency for identifier-shaped
values would be a benchmark-shaped bypass of a pinned identity-safety
rule. **No production change.** Details:
`docs/phase_4/party_boundary_audit.json`; regression:
`fte_invoice_party_boundary_test.py` (27 checks).

## 6. Invoice integration architecture (verified, not assumed)

Invoice documents enter `orchestrate()` after `vy_harden`; the narrow
path fires only when labels exist, normalization is clean (or the
documented narrow payment+outstanding reconciled bypass applies), roles
are unambiguous, reconciliation holds, and the composed journal balances
against the labelled total. Any violation falls through to the
UNCHANGED narration path. Role evidence (`invoice_roles`) is attached to
every invoice-path result. Ownership evidence integrates into
`_assign_ownership` ahead of the `transaction_value` fallback.

## 7. Authority routing matrix

Machine-checked against the live registry and observed routing
(`authority_boundary_matrix.json`):

| Capability | Status | Routes to | Test evidence |
|---|---|---|---|
| KERNEL.INVOICE_PURCHASE | SUPPORTED | existing purchase machinery (Purchases / ASSET_AUTHORITY asset path / expense account) | P01–P05, G01–G06, M01, D01, D06/D07 |
| KERNEL.INVOICE_SALE | SUPPORTED | existing sale convention (DR party / CR Sales + Output GST) | S01–S04; S05 boundary |
| KERNEL.INVOICE_GST_AMOUNTS | SUPPORTED | GST_AUTHORITY conventions (components / IGST single line) | G01–G05 + X5 |
| KERNEL.INVOICE_PAYMENT_AGAINST | SUPPORTED | SETTLEMENT_AUTHORITY (posts only the payment) | M02/M03 + X4 |
| KERNEL.INVOICE_REFUND | SUPPORTED | existing refund machinery | role gate L.x |
| KERNEL.INVOICE_CREDIT_NOTE | SUPPORTED | existing purchase-return machinery | role gate M.x |
| KERNEL.INVOICE_OUTSTANDING_EVIDENCE | PARTIAL | settlement evidence / review path | K.1 + M02 bypass trace |
| KERNEL.INVOICE_TAX_INCLUSIVE | UNSUPPORTED | refused | D09 + case G |
| KERNEL.INVOICE_DEBIT_NOTE | UNSUPPORTED | refused | D10 + case H |

Every SUPPORTED entry carries implementation_ref + test_ref +
required_inputs + limitations; UNSUPPORTED entries carry refusal
evidence. Registry total: 53 capabilities (C-6 pin).

## 8. Invoice fixture matrix (Phase 3 spec, 30 fixtures)

Purchases 1–5, Sales 6–10, GST 11–16, Payment 17–20, Defensive 21–30 —
each asserted per stage (recognition, role resolution, reconciliation,
grounding/normalization, authority routing, execution, verdict).
Result: **30/30 match expected**; 20 VERIFIED, 7 REVIEW_REQUIRED, 2
NOT_SUPPORTED, 1 INVALID_INPUT_MATH (the narration math gate's stricter
refusal of `paid + outstanding != total`). Notable documented behavior:
S02 — sale composes GROSS (party debit / revenue credit never netted);
the paid label is evidence, so part-collection on a sale is recorded as
a Phase 5 candidate. `invoice_fixture_matrix.json` is generated from
live `stage_trace()` runs.

## 9. False-VERIFIED results (Phase 5)

Cases A–J plus structural invariants: **50/50 PASS,
false_VERIFIED = 0** (`false_verified_matrix.json`). Highlights:
B/C/D refuse with explicit reasons and zero journal lines; E/F prove
code/date digits never become amounts (both at role layer and
`_extract_amounts`); G/H match their registry contracts
(NOT_SUPPORTED); I refuses service wording while the asset/expense
machinery still decides when it CAN; J refuses both-sides documents and
composes single-side documents. Invariants: unsupported/conflicting/
insufficient evidence can never become VERIFIED; a normalization
concern refuses before any invoice code runs (the ungated executor
composes — proving the gate sits ABOVE it); every VERIFIED routes under
a real authority and carries balanced, evidenced journals.

## 10. Full regression results (Phase 7)

| # | Gate | Result | Classification |
|---|---|---|---|
| 1 | P0 | PASS 138/138 | — |
| 2 | Batch-1 | PASS | — |
| 3 | Boundary closure | PASS 852/852 | — |
| 4 | GST | 86 pass / 2 fail | **pre-existing** (F.6, H.1) |
| 5 | Core maths | PASS 202/202 | — |
| 6 | Capability registry | PASS 220/220 | — |
| 7 | Doc-understanding P1 | PASS 63/63 | — |
| 8 | Doc-understanding P2 | PASS 46/46 | — |
| 9 | Doc-understanding P3 | 50/51 | **pre-existing** |
| 10 | Student production gate | PASS | — |
| 11 | Invoice-role gate | PASS 79/79 | — |
| 12 | Party-boundary gate | PASS 27/27 | — |
| 13 | Integration gate | PASS 249/249 | — |
| 14 | False-VERIFIED gate | PASS 50/50 | — |
| 15 | Phase-4 integration matrix | PASS 30/30 fixtures | — |

Frozen artifacts: `git status`/`git diff` clean for
`benchmark/real_invoice_e2e/` — no unexpected change; no STOP condition.

## 11. Real E2E (run once, stub replay model)

- Final: **0 VERIFIED / 30 REVIEW_REQUIRED** / 0 BLOCKED / 0
  UNSUPPORTED_TRANSACTION / 0 VALIDATION_FAILED / 0 GROUNDING_FAILED /
  0 FORBIDDEN_OUTPUT / 0 MODEL_UNAVAILABLE.
- MODEL: schema validity 30/30 (build-time gate); transaction-type match
  30/30; status agreement vs gold 3/30 (the 3 intentionally
  REVIEW_REQUIRED gold cases agree).
- INVOICE SEMANTICS: role extraction reachable on all documents; on this
  corpus 30/30 are blocked upstream of semantics by the party-ID
  concern, so label-level accuracy is not measurable on this fixture set
  (measured instead on the 30-fixture matrix: 30/30).
- AUTHORITY: 30/30 refused before authority routing (party evidence);
  0 mis-routes; 0 invented accounts.
- SAFETY: **false_VERIFIED 0; unsupported VERIFIED 0; invented amount 0;
  invented party 0; invented account 0; grounding bypass 0.**
- Gold mismatch documented: 27 gold VERIFIED cases cannot be reached
  without violating the pinned party-identity safety rule; the corpus
  fixture (not the rule) is the defect for those cases. Kernel unchanged.

## 12. Frozen-artifact integrity

`benchmark/real_invoice_e2e/` byte-identical to `79edb78`
(corpus SHA `b861f91e…`, manifest SHA `d9e03c7a…`). No regeneration in
Phase 4.

## 13. Remaining limitations

Unchanged from Phase 3 registry: tax-inclusive pricing, debit notes,
GST reversal on credit notes, POS cash sales, supplier-side refunds,
sales-return expansion, rate derivation from amounts, multi-currency,
part-collection on sales (S02), standalone freight documents. Plus:
benchmark corpus party fields use identifier-shaped values that pinned
safety rules will always refuse.

## 14. Phase-5 candidates (documented, NOT implemented)

1. Part-collection posting on sales invoices (S02 note).
2. Corpus regeneration with realistic party NAMES (unlocks the 27 gold
   VERIFIED cases without touching safety).
3. Freight-only documents (standalone carriage-in capability).
4. Instrument-aware settlement accounts (Bank vs Cash).

## 15. Git status

Phase 3 commit `79edb78` is HEAD of `main` (pushed). Phase 4 changes are
uncommitted working-tree changes (2 production files modified, 3 gates +
5 docs added); pre-existing unrelated user changes remain untouched.
No commit made in Phase 4 (not requested).

## 16. Commit hash

Phase 3 baseline: **`79edb78`**. Phase 4: uncommitted (awaiting
instruction).

---

### Failure classification summary

| Failure | Suite | Classification |
|---|---|---|
| F.6 inclusive-GST | GST 15K | pre-existing (Phase 1 baseline) |
| H.1 corpus hash | GST 15K | pre-existing (proven independent of invoice changes) |
| "digital: reached the real kernel" | DU P3 | pre-existing (reproduced with invoice path disabled) |

No Phase-4 regressions. No STOP conditions triggered. The end-state
architecture holds: invoice is a semantic input domain, NOT a new
accounting authority.
