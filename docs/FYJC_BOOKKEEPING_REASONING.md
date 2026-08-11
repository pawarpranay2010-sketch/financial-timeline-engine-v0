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

---

# SPRINT 15E — FYJC BOOK-KEEPING UNIT-TEST-1 TEXTBOOK COVERAGE

**Sprint:** 15E — Book-Keeping Unit-Test-1 Textbook Coverage
**Verdict:** ✅ **PASS** — 30/30 gate, 101/101 golden oracles, 0 invented
accounts, 0 fabricated amounts, deterministic & C++-authoritative

## E.1 Syllabus boundary (verified, not assumed)

The Unit-Test-1 scope is the **first three FYJC Book-Keeping & Accountancy
chapters** as declared by the golden benchmark
(`backend/maths/fyjc_bk_15e_benchmark.py`):

| Ch | Chapter | Scope in FT-E |
|----|---------|---------------|
| 1 | Introduction to Book-Keeping & Accountancy | business entity, double entry, Real / Personal / Nominal classification |
| 2 | Basic Accounting Terms / accounting equation | capital, drawings, debtors, creditors, purchases, sales, assets, expenses, incomes, discounts |
| 3 | Journal | the complete basic transaction family + multi-transaction questions |

Later-year topics are explicitly **outside** the boundary and refused as
NOT_SUPPORTED: final accounts / trading & profit & loss, balance sheet,
partnership, depreciation, provisions / RDD, bad debts write-offs beyond
the basic family, insolvency, consignment, joint venture, issue of shares,
opening entries for a new year, etc.

## E.2 Unit-Test-1 capability matrix

| Chapter | Concept | Question pattern (supported wording family) | Supported? | Reasoning method | Output type | Refusal condition |
|---------|---------|--------------------------------------------|:----------:|------------------|-------------|-------------------|
| 1–2 | Business start | `Started / commenced / began business with cash / bank balance / assets` (incl. `cash + furniture`, `cash + bank`, single asset) | ✅ | START_BUSINESS + `_startup_asset_breakdown` | Cash/Bank/asset Dr · Capital Cr (compound start splits named components) | amount missing → BLOCKED; >1 named asset split never guessed |
| 1–2 | Additional capital | `Brought in additional capital`, `introduced X as capital`, `brought X into the business` (X = asset or cash) | ✅ | CAPITAL_INTRODUCED / CAPITAL_ASSET_INTRODUCED | exact asset (or cash/bank) Dr · Capital Cr | >1 asset → ASSET_AMBIGUOUS refusal |
| 2–3 | Drawings | `Withdrew cash for personal/private use`, `withdrew goods worth … for personal use`, `goods taken by the proprietor` | ✅ | DRAWINGS_CASH / GOODS_PERSONAL_USE | Drawings Dr · Cash/Bank Cr; goods → Drawings Dr · Purchases Cr | amount missing → BLOCKED |
| 3 | Cash purchase | `Purchased/bought goods for cash`, `goods purchased for … in cash`, `purchased goods costing … payment made immediately`, `by cheque` | ✅ | PURCHASE_GOODS_CASH | Purchases Dr · Cash/Bank Cr | amount missing → BLOCKED; cash vs credit ambiguous → REVIEW_REQUIRED |
| 3 | Credit purchase | `purchased/bought goods from <party> on credit`, `for Rs.X on credit`, `goods worth Rs.X from <party>`, `bought goods on credit from <party>` | ✅ | PURCHASE_GOODS_CREDIT | Purchases Dr · <party> Cr | amount missing → BLOCKED; no party → REVIEW_REQUIRED |
| 3 | Cash sale | `sold goods for cash`, `sold goods to <party> for cash`, `cash sale of goods`, `goods sold and cash received immediately`, `goods costing … sold … for cash` (cost dropped) | ✅ | SALE_GOODS_CASH | Cash/Bank Dr · Sales Cr (party never becomes debtor) | amount missing → BLOCKED |
| 3 | Credit sale | `sold goods to <party> on credit`, `sold to <party> for Rs.X on credit`, `goods costing … sold … on credit` | ✅ | SALE_GOODS_CREDIT | <party> Dr · Sales Cr | amount missing → BLOCKED; no party → REVIEW_REQUIRED |
| 3 | Fixed-asset purchase/sale | `purchased furniture/machinery/building/land for cash/on credit/by cheque`, `sold old furniture for cash`, `sold … received a cheque` | ✅ | PURCHASE_ASSET_* / SALE_ASSET_* (exact asset only) | exact named asset ± Cash/Bank/party | >1 asset → refused; mode unstated → REVIEW_REQUIRED |
| 3 | Expenses | `paid rent/salary/wages/electricity/insurance/stationery/repairs/postage/legal fees/audit fees/income tax`, `paid for <expense> in cash`, `by cheque`, `paid carriage inward/outward` | ✅ | EXPENSE_PAID (+ `_EXPENSE_ACCOUNT_WORDS`, longest phrase first) | expense A/c Dr · Cash/Bank Cr | amount missing → BLOCKED; unrecognised expense word → REVIEW_REQUIRED |
| 3 | Incomes | `received commission/interest/rent/dividend`, `commission received in cash`, `interest received by cheque` | ✅ | INCOME_RECEIVED (+ `_INCOME_ACCOUNT_WORDS`) | Cash/Bank Dr · income A/c Cr | amount missing → BLOCKED |
| 3 | Bank / cash | `deposited cash into bank`, `withdrew cash from bank`, `paid to <party> in cash`, `received from <party> in cash`, cheque payment/receipt | ✅ | CASH_INTO_BANK / CASH_FROM_BANK / PAID_TO / RECEIVED_FROM / CHEQUE_PAID / CHEQUE_RECEIVED | contra (Bank Dr · Cash Cr etc.) or party ± Cash/Bank | amount missing → BLOCKED |
| 3 | Trade discount | `…for Rs.X at N% trade discount` (netting only, never a cash-discount line) | ✅ | trade-discount pipeline | list price → −N% → net value posted | no amount → BLOCKED |
| 3 | Cash discount / partial payment | `half the amount paid immediately`, `N% cash discount on the amount paid`, `paid him Rs.X immediately` | ✅ | paid/credit split → cash discount on paid portion only (chronological, traced) | exact cash-paid / discount-received / party-balance lines | fraction or % unreadable → REVIEW_REQUIRED |
| 3 | Discount settlements | `received from <party> Rs.X, discount allowed Rs.Y`, `paid … discount received … in full settlement of Rs.Z`, `allowed him discount Rs.Y` | ✅ | explicit-discount mapping (positional: cash / discount / party total) | Cash + Discount Allowed Dr · <party> Cr (or mirror) | discount without settlement context → REVIEW_REQUIRED |
| 3 | Returns | `returned goods to <party>`, `<party> returned goods`, `purchases returns to …`, `sales returns from …`, `returned goods worth … to him` | ✅ | `_returns_rule` (structural) + multi-transaction party inheritance | <party> Dr · Purchase Returns Cr / Sales Returns Dr · <party> Cr | party-less standalone return → REVIEW_REQUIRED |
| 3 | Multi-transaction | `Start… . Purchased… . Paid… .` / `Sold … . Received from him … discount allowed` | ✅ | `_split_transactions` (sentence + return-boundary splitting) → independent chronological journals → merged ledger/TB | N independent journal entries + one ledger + one trial balance | any segment missing amount → BLOCKED (no partial fabrication) |

## E.3 What changed in the engine (Sprint 15E delta)

* **`classify_bk_type`** — bank-only business start posts to **Bank**;
  capital-asset introductions (`brought machinery into the business`)
  debit the exact asset; `goods worth Rs.X from <party>` credit purchases;
  `paid for <expense>` expenses; `by cheque` / `payment made immediately`
  full-settlement modes; `goods costing Rs.X sold for cash Rs.Y` keeps the
  sale price (cost dropped in the amount pipeline); sentence-opening
  `discount received from <party>` is a DISCOUNT entry.
* **`_returns_rule`** — goods returns told apart by structure
  (`…returned … to <party>` = purchase return; `<party> returned goods …` =
  sales return) instead of enumerating wordings.
* **`_split_transactions`** — honorific titles (`Mr. Sharma`) no longer
  split; comma-joined returns become their own transaction; a party-less
  `returned goods worth Rs.X` inherits the previous segment's party
  deterministically.
* **`_detect_explicit_discount`** — positional mapping of cash / discount /
  party-total figures, settlement phrases (`in full settlement of`, `his
  account of`, `being`), pronoun-resolved `allowed <party> discount Rs.Y`,
  and derivation of an unstated discount by subtraction when both figures
  are stated (never an invented number).
* **Settlement-mode hardening** — `cash discount of N%` never flips a
  credit purchase into cash mode, and a PARTIAL payment
  (`half … paid immediately`) never triggers full-immediate settlement
  (`_full_immediate_settlement` + cash-discount stripping in
  `has_cash_mode`).

## E.4 Sprint 15E verification

| Gate | Result |
|------|--------|
| Sprint 15E Unit-Test-1 Gate (`scripts/fte_fyjc_bk15e_test.py`) | ✅ **30/30** — 122-case golden benchmark (101 verified oracles, 21 refusals) |
| Sprint 15B Gate (`scripts/fte_fyjc_bk15b_test.py`) | ✅ **388/388** |
| Sprint 15C P0 Gate (`scripts/fte_fyjc_bk_p0_test.py`) | ✅ **138/138** |
| Sprint 15D Derivation Gate (`scripts/fte_fyjc_15d_test.py`) | ✅ **191/191** |
| Sprint 15 Stage 4 Routing (`scripts/fte_fyjc_routing_regression_test.py`) | ✅ **44/44** |

Hard invariants re-asserted: 0 invented accounts · 0 fabricated amounts ·
0 unbalanced VERIFIED journals · 0 unbalanced VERIFIED trial balances ·
0 formula_id=None confident results · C++ authority intact (registered
metrics only) · identical input → identical output.

---

# SPRINT 15F — FYJC BOOK-KEEPING CH.1–3 TEXTBOOK PATTERN EXPANSION

**Sprint:** 15F — Book-Keeping Ch.1–3 Reusable Pattern Expansion
**Verdict:** ✅ **PASS** — 43/43 gate, 162-case hand-verified benchmark
(150 benchmark + 12 student-error), 100% pattern coverage, 0 safety
violations, deterministic & C++-authoritative

## F.1 Exact Ch.1–3 boundary (unchanged from 15E — never silently expanded)

| Ch | Chapter | Scope in FT-E |
|----|---------|---------------|
| 1 | Introduction to Book-Keeping & Accountancy | business entity, double entry, Real / Personal / Nominal classification |
| 2 | Basic Accounting Terms / accounting equation | capital, drawings, debtors, creditors, purchases, sales, assets, expenses, incomes, discounts |
| 3 | Journal | the complete basic transaction family + multi-transaction questions |

Still **NOT_SUPPORTED** (outside the boundary, never answered):
depreciation, final accounts, Trading/P&L, balance sheet, partnership,
opening entries, issue of shares, consignment, hire purchase, revaluation,
provisions/RDD. The 15F gate asserts 12/12 of these refusals.

## F.2 What changed in the engine (Sprint 15F delta)

All changes are registry/rule-driven — no per-sentence handlers were added.

| # | Rule | Fix |
|---|------|-----|
| 1 | **`on account` = `on credit`** | A credit-mode branch now classifies `Bought goods on account from Rahul` and `Sold goods on account to Mohan` as credit purchases/sales (the amount may sit between the goods word and the party) |
| 2 | **Cheque deposit is never cash** | `Cheque deposited into bank` previously posted Bank Dr / **Cash** Cr (a silent substitution). Now: with a named drawer → `CHEQUE_DEPOSITED` (Bank Dr / drawer Cr); without → REVIEW_REQUIRED |
| 3 | **`the bank` wording variants** | `Deposited cash into the bank`, `Withdrew cash from the bank` etc. now classify as CASH_INTO_BANK / CASH_FROM_BANK |
| 4 | **`Payment made for <expense>`** | The expense family now covers the payment-noun phrasing (`Payment made for rent in cash`) |
| 5 | **`cheque in favour of <party>`** | `_party_from_text` extracts the party after `in favour of` / `in favor of` so `Issued a cheque in favour of Amit` posts Amit/Bank |
| 6 | **Debtor-subject payments** | `Mohan paid Rs.12,000` / `Mohan paid us Rs.4,000` are RECEIPTS (Cash Dr / Mohan Cr) — the party's subject position before `paid` decides direction; `Rahul paid rent` stays an expense |
| 7 | **Payment-fraction vs discount-rate collision** | `_paid_fraction` no longer reads a `<n>%` trade/cash-discount rate as the paid portion: `at 25% trade discount; paid three-fourths immediately` now pays 75% (word fractions) and `paid 50% immediately` after a 15% trade discount pays 50% (tight ±12-char window) |
| 8 | **Cheque-receipt typing** | `Cheque received from Mohan` / `Received a cheque from Mohan` both classify `CHEQUE_RECEIVED`; `Interest received by cheque` stays an income |
| 9 | **Party sale + partial collection** | `Sold goods to Mohan …; received cash for half at once` is a CREDIT sale with partial collection (Mohan stays a debtor) — the `cash` word describes the collection, not the sale mode |
| 10 | **`Received Rs.5,000.`** | Now REVIEW_REQUIRED (parallel to `Paid Rs.5,000.`) — never NOT_SUPPORTED, never invented context |

## F.3 Reusable pattern library & coverage report (spec §2 / §16)

* `BK_PATTERN_LIBRARY` in `backend/maths/fyjc_bk_15f.py` — one record per
  canonical pattern: pattern_id, description, example category, required
  inputs, account structure (Debit/Credit), Golden Rule, journal
  structure, ledger effect, trial-balance effect, supported wording
  variants, refusal conditions.
* `pattern_coverage_report()` + `write_coverage_report()` emit the
  machine-readable `docs/fyjc_bk_15f_coverage.json` and the
  human-readable `docs/FYJC_BK_15F_COVERAGE.md`.

**Coverage result: 150/150 cases pass across 29 pattern buckets**
(25 transaction patterns + MULTI_TRANSACTION + 3 refusal buckets).

## F.4 Student-answer verification (spec §12 — first deterministic mistake)

`verify_student_journal` / `verify_student_final` in
`backend/maths/fyjc_bk_15f.py`, reusing the existing
`verify_ledger_balance` / `verify_trial_balance`: the reference comes from
the FULL pipeline (journal → ledger → trial balance, multi-transaction
aware) and the ordered checks report the FIRST failure — not merely
“wrong”: structure → totals balance (exact discrepancy) → debit accounts
→ credit accounts → per-line amounts → Real/Personal/Nominal
classification. The 12-case student-error section covers: correct and
reversed journals, wrong amounts, unbalanced journals, hallucinated
accounts (Machinery), cash-sale debtor confusion, ledger balances and
trial-balance rows, and final-answer-only checks (journal total, TB total,
per-account debit, ledger balance with Dr/Cr side).

## F.5 Sprint 15F verification

| Gate | Result |
|------|--------|
| Sprint 15F Ch.1–3 Pattern Gate (`scripts/fte_fyjc_bk15f_test.py`) | ✅ **43/43** — 162-case benchmark (150 oracles + 12 student-error), 150/150 benchmark, 12/12 student checks, 24/24 refusals, exact-account 0 violations, determinism repeatable |
| Sprint 15E Unit-Test-1 Gate | ✅ **30/30** |
| Sprint 15D Derivation Gate | ✅ **191/191** |
| Sprint 15C P0 Gate | ✅ **138/138** |
| Sprint 15B Gate | ✅ **388/388** |
| Stage 4 routing | ✅ **44/44** |
| Sprint 15 Pilot | ✅ PASS (11/11 maths, 9/9 refusals, 20/20 bk, unsafe 0) |
| Sprint 14 UI gate + UI AppTest | ✅ PASS |
| Sprint 13 readiness (incl. embedded 12A–12F regression) | ✅ 504/504 |
| Maths core / 12F production gate / reasoning / decision graph | ✅ 202/202 · PASS · 123/123 · 99/99 |
| Formula engine Python + C++ bridge / C++ self-test | ✅ ALL CHECKS COMPLETE |
| Student workspace / AppTest / Demo | ✅ 98/98 · PASS · ALL CHECKS COMPLETE |
| `py_compile` + `compileall` + `git diff --check` | ✅ clean |

Hard invariants re-asserted: 0 invented accounts · 0 fabricated amounts ·
0 wrong-concept confident answers · 0 silent substitutions · 0 unbalanced
VERIFIED journals · 0 unbalanced VERIFIED trial balances · 0
formula_id=None confident results · 0 C++ authority violations · 0
confident answers outside the declared syllabus · identical input →
identical output.
