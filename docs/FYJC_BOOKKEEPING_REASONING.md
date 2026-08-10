# FYJC BOOK-KEEPING REASONING — SPRINT 15B IMPLEMENTATION REPORT

**Component:** Financial Timeline Engine (FT-E) — FYJC Student section
**Sprint:** 15B — Book-Keeping Question Understanding & Reasoning Hardening
**Date:** 2026-08-10
**Verdict:** ✅ **PASS** — deterministic, C++-authoritative, hallucination-safe

---

## 1. Objective

Harden the complete FYJC Book-Keeping & Accountancy reasoning pipeline so
FT-E can understand realistic FYJC textbook-style questions and produce a
correct, student-readable solution:

```
Photo / PDF / typed question
  -> extracted / normalised question            (wording variants collapse
                                                 to ONE canonical type)
  -> transaction / question type                (registry-driven, first
                                                 match wins)
  -> EXACT account identification               (never invents Machinery
                                                 for a Furniture purchase)
  -> Real / Personal / Nominal classification   (traditional syllabus)
  -> traditional Golden Rule                    (debit/credit decision + WHY)
  -> journal entry                              (date, particulars, Dr/Cr,
                                                 amount, narration)
  -> ledger reasoning                           (derived from journal IR)
  -> trial balance reasoning                    (Dr == Cr or exact
                                                 discrepancy - never forced)
  -> trade / cash discount + partial-payment    (chronological pipeline,
                                                 exact traced numbers)
  -> C++ mathematical verification              (registered metrics only,
                                                 authority_state == cpp)
  -> refusal boundaries                         (BLOCKED / REVIEW_REQUIRED /
                                                 NOT_SUPPORTED)
  -> student-facing "What FT-E understood"
```

## 2. Files

| File | Type | Change |
|------|------|--------|
| `backend/maths/fyjc_accounting.py` | Modified | Sprint 15B exact-asset engine: `named_assets` (span-aware, longest phrase wins - an `office equipment` purchase can never also produce `Equipment`), `_asset_purchase_rule`, `_asset_sale_rule`, `_party_from`, `_collapse_cash_bank`, `_resolve_side`, `classify_transaction`, `identify_debit_credit`, `verify_journal_entry`, `post_ledger`, `verify_ledger_balance`, `build_trial_balance`, `verify_trial_balance`, `verify_arithmetic`, `accounting_calculation` |
| `backend/maths/fyjc_bk_reasoning.py` | New | The hardened Book-Keeping reasoning pipeline (see below) |
| `scripts/fte_fyjc_bk15b_test.py` | New | Sprint 15B regression gate (388 deterministic checks) |
| `docs/FYJC_BOOKKEEPING_REASONING.md` | New | This report |

## 3. What the reasoning module provides

**Question understanding (section 1).** ~30 canonical transaction patterns
(registry-driven): business start / capital, drawings (cash + goods),
goods purchase/sale (cash, credit, cheque, with or without a named party),
fixed-asset purchase/sale (exact asset only), expenses, incomes, bank
deposits/withdrawals, cheque payments/receipts, returns, discounts, loans,
bad debts, goods for personal use, free samples, interest on capital and on
drawings. Equivalent wording (`Purchased goods for cash` / `Goods purchased
for cash` / `Bought goods for cash` / `Purchased goods from Amit for cash`)
collapses to the same deterministic intermediate representation.

**Cash-mode override.** `for cash` decides the settlement mode even when a
party is named: `Sold goods to Mohan for cash` is a CASH sale
(Cash A/c Dr / Sales A/c Cr), never a credit sale to Mohan. Contradictory
`cash ... on credit` wording falls through to the refusal layer.

**Exact account identification (section 2).** `Purchased Furniture for Cash
₹15,000` produces exactly Furniture A/c Dr / Cash A/c Cr. The asset account
comes only from the asset word actually present; a question naming two
assets (`Purchased machinery and furniture for cash`) is refused as
ambiguous - FT-E never guesses the split. Regression-tested against the
Furniture/Machinery/Building/Vehicle hallucination class.

**Traditional FYJC reasoning (section 3).** Every account is classified
Real / Personal / Nominal with the traditional Golden Rules and a
student-readable WHY per line:

```
Dr  Furniture  15,000  [Real]      Furniture (Real A/c): it comes in - Debit what comes in.
Cr  Cash       15,000  [Real]      Cash (Real A/c): it goes out - Credit what goes out.
Cr  Rahul       9,000  [Personal]  Rahul (Personal A/c): Rahul is the giver - Credit the giver.
Dr  Purchases  10,000  [Nominal]   Purchases (Nominal A/c): it is an expense/loss - Debit expenses and losses.
```

**Journal / ledger / trial balance (sections 4-6).** A VERIFIED journal is
always balanced (`total_debit == total_credit`); the ledger is derived
*only* from the journal IR (never re-interpreted); the trial balance is
built from the ledger state and exposes the exact discrepancy when
Dr != Cr - it is never forced into balance. Every numeric step carries a
`calculation_id` provenance record (`BK_LIST_PRICE`, `BK_TRADE_DISCOUNT_AMOUNT`,
`BK_NET_TRANSACTION_VALUE`, `BK_PAID_CREDIT_SPLIT`, `BK_CASH_DISCOUNT_AMOUNT`,
`BK_CASH_PAID_NET`, `BK_EXPLICIT_DISCOUNT`).

**Multi-transaction questions.** `;`-separated segments are journaled
independently; a following `him/her/them` resolves to the party named by the
previous segment; a payment/discount step (`paid half immediately with 2%
cash discount`, `paid him ₹4,000`) is folded into the previous journal
through the full discount pipeline - it is never posted as an independent
entry. One ledger and one trial balance are produced for the whole question.

**Discount pipeline (section 7).** Chronological and traced:

```
Total/List Price -> deduct Trade Discount -> Net Transaction Value
-> split paid vs credit portion -> apply Cash Discount on the paid
portion only -> final journal/ledger values -> C++ verification
```

Example: `Purchased goods from Rahul ₹10,000 on credit with 10% trade
discount; paid half immediately with 2% cash discount` resolves exactly to:

```
Dr  Purchases        9,000.00
Cr  Cash             4,410.00      (4,500 paid - 90 cash discount)
Cr  Discount Received   90.00      (2% of 4,500)
Cr  Rahul            4,500.00      (credit remainder)
```

Explicit discount-amount settlements (`Received from Mohan ₹9,800, discount
allowed ₹200` → Cash 9,800 + Discount Allowed 200 / Mohan 10,000) are
handled separately from percentage discounts and never misread as partial
payments. A discount with no settlement context, and a `full settlement`
without a stated discount amount, are both refused.

**C++ authority (section 8).** Registered financial metrics arising from a
book-keeping question go through `verify_bk_metric` → `verify_maths_answer`
→ the C++ formula engine, returning `authority_state == cpp` with a valid
`formula_id`. It is structurally impossible to produce a DERIVED/VERIFIED
result with `formula_id == None`. The posting arithmetic in this module is
explicitly *verification/preparation* arithmetic with full provenance -
Python never computes a registered metric.

**Refusal boundaries (section 9).** Missing amount → BLOCKED; ambiguous
cash/credit or unstated discount → REVIEW_REQUIRED; outside the FYJC
syllabus boundary (depreciation, final accounts, issue of shares, etc.) →
NOT_SUPPORTED. Refusals never carry journal lines or a confident display.

## 4. Sprint 15B continuation fixes

The regression probes exposed routing defects that were fixed in this
pass:

| # | Defect | Fix |
|---|--------|-----|
| 1 | `Sold goods to Mohan for cash` routed to a credit sale (Dr Mohan) | Cash-mode override: `for cash` decides the mode even with a named party |
| 2 | `Goods purchased on credit from Rahul` → NOT_SUPPORTED | Added `goods purchased/bought ...` phrase family to the credit-purchase pattern |
| 3 | `Purchased stationery for cash` / `Paid telephone bill` → NOT_SUPPORTED | Added stationery-purchase and telephone-bill expense phrases + account words |
| 4 | Bare `Purchased goods.` refused as NOT_SUPPORTED | Now REVIEW_REQUIRED ("does not say whether for cash or on credit") |
| 5 | `Received ... discount` questions routed to INCOME_RECEIVED | Discount phrases owned exclusively by the DISCOUNT_RECEIVED pattern |
| 6 | Explicit-discount settlements showed a misleading paid/credit split in "What FT-E understood" | The naive split is skipped when an explicit discount settles the account |
| 7 | `Interest on drawings charged` routed to the cash-withdrawal pattern (bare `drawings` phrase) | Dedicated pre-rule so `interest on drawings` is its own transaction |
| 8 | `Goods taken for personal use` credited Cash instead of Purchases | `goods ... personal use` pre-rule wins over the generic withdrawal phrase |
| 9 | `Office equipment` purchase produced a phantom `Equipment` account | `named_assets` is span-aware: a phrase inside a longer match is not a second asset |
| 10 | `Repaid the loan` → NOT_SUPPORTED | Added `repaid the loan` phrase |

## 5. Verification matrix

| Gate | Result |
|------|--------|
| Sprint 15B Book-Keeping Reasoning Gate (`scripts/fte_fyjc_bk15b_test.py`) | ✅ **388/388** |
| Sprint 15 Stage 4 Routing Gate (`scripts/fte_fyjc_routing_regression_test.py`) | ✅ 44/44 — unsafe confident answers 0, C++ authority violations 0, fabricated values 0, silent substitutions 0 |
| Sprint 15 40-question Pilot Gate (`scripts/fte_fyjc_pilot_test.py`) | ✅ PASS — maths 11/11, refusals 9/9, book-keeping 20/20, unsafe 0, invariants 10/10, cpp match 11/11 |
| Sprint 13 FYJC Readiness (`scripts/fte_fyjc_readiness_test.py`) | ✅ 504/504 |
| `py_compile` (modified/new modules + gate) | ✅ |

Hard invariants asserted by the gate: deterministic repeatability; every
VERIFIED journal balanced with positive amounts and `BK_LIST_PRICE`
provenance; refusals never carry journal lines; registered metrics resolve
with `authority_state == cpp` and a non-empty `formula_id`; unsupported
metrics are refused with `formula_id == None`.

## 6. Boundary

* No second maths engine, no LLM, no fallback Python calculation for
  registered metrics - C++ remains the sole mathematical authority.
* The legacy Sprint 13 `classify_transaction` surface is unchanged (the
  probe baseline still shows its original outputs); the new pipeline sits
  alongside it and the pilot/readiness gates confirm zero regression.
* UI wiring of the new pipeline is out of scope for this sprint; the module
  exposes a single deterministic entry point (`reason_bk_question`) ready
  for the student-flow integration sprint.
