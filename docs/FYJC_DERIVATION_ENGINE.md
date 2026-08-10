# FYJC Formula / Rule Derivation & Textbook Coverage Engine (Sprint 15D)

The FYJC Student section of FT-E is extended from a collection of manually
registered question patterns into a **registry-driven reasoning system**:

```
ONE CANONICAL TRUTH
        ↓
DERIVATION / RULE COMPOSITION
        ↓
VALIDATED SOLUTION PATHS
        ↓
QUESTION INTENT
        ↓
DEPENDENCY RESOLUTION
        ↓
C++ AUTHORITY
        ↓
JOURNAL / LEDGER / MATH RESULT
        ↓
STUDENT-READABLE EXPLANATION
```

The system remains deterministic, auditable, C++-authoritative and
fail-closed. It **extends** the existing Sprint 13-15C FYJC architecture; it
does not replace it, and it does not add a second maths engine, an LLM, or a
Python fallback calculation.

---

## 1. Canonical Formula Registry — `backend/maths/fyjc_canonical.py`

One centralized registry of canonical FYJC relationships. Each entry carries
full metadata:

- `formula_id`
- `canonical_formula` (e.g. `Profit = Revenue - Expenses`)
- `variables` / `dependencies`
- `unit_kind` (`amount` | `percent`)
- `percentage_semantics` (`rate_is_percent_number` - the rate is written as
  the display number, e.g. 5 for 5%)
- `supported_targets` (every variable the relationship can be solved for)
- `academic_topic` (e.g. `Commercial Arithmetic - Commission`)
- `solution_methodology`
- `version`
- `provenance`
- `validation_status` (`VALIDATED` only after independent numeric validation)

A single canonical relationship represents every derived path, never one
hardcoded formula per direction:

| Canonical | Derived paths |
|---|---|
| `Profit = Revenue - Expenses` | Revenue, Expenses |
| `Loss = Expenses - Revenue` | Expenses, Revenue |
| `Commission = Sales × Commission Rate ÷ 100` | Sales, Commission Rate |
| `Trade Discount = List Price × Trade Discount Rate ÷ 100` | List Price, Trade Discount Rate |
| `Cash Discount = Paid Amount × Cash Discount Rate ÷ 100` | Paid Amount, Cash Discount Rate |
| `Net Price = List Price - Trade Discount` | List Price, Trade Discount |
| `Cash Paid = Paid Amount - Cash Discount` | Paid Amount, Cash Discount |
| `Creditor Balance = Net Purchase - Amount Paid` | Net Purchase, Amount Paid |
| `Debtor Balance = Net Sale - Amount Received` | Net Sale, Amount Received |
| `Selling Price = Cost Price + Profit` | Cost Price, Profit |
| `Profit Percent = Profit ÷ Cost Price × 100` | Cost Price, Profit |
| `Loss Percent = Loss ÷ Cost Price × 100` | Cost Price, Loss |
| plus the existing 12A-12F relationships (Profit Margin, Net Margin, ROE, ROA, EPS, Gross Profit) | |

The same module registers the **Book-Keeping canonical rules**
(`BK_RULES`, `golden_rule_for`, `compose_transaction_rule`):

- Real — *Debit what comes in. Credit what goes out.*
- Personal — *Debit the receiver. Credit the giver.*
- Nominal — *Debit expenses and losses. Credit incomes and gains.*

`FYJC_FORMULA_REGISTRY` is the executable registry handed to the strict
C++-authority solver (`Solver(..., cpp_authority=True)`).

## 2. Controlled Derivation Engine — `backend/maths/fyjc_derivation.py`

A deterministic algebraic layer over the canonical registry:

1. Parse the canonical expression (existing `parse_expression` AST).
2. Identify the requested target variable.
3. Algebraically isolate the variable (single-occurrence inversion only).
4. Generate the derived expression.
5. Validate the transformation numerically (forward + reverse vectors).
6. Assign a derived path ID (`<CANONICAL_ID>::<TARGET>`).
7. Record the derivation in the deterministic audit trail.
8. **Reject** any transformation it cannot prove safely
   (`DerivationUnsupported`, multi-occurrence variables, variables in
   exponents, tampered expressions).

`validate_derived_path` is **independent** of the derivation itself: a
derived expression becomes `VALIDATED` only when deterministic numeric
vectors reproduce the canonical relationship exactly (with a
relative-tolerance comparison for Decimal round-trip noise). A wrong
expression is `REJECTED` - never `VERIFIED` merely because it was derived.

`solve_derived` executes the validated path **through the existing C++
authority** (the same `cpp_calculate` / `cpp_solve_metric` bridge) and
returns a `Solution`-shaped record with `formula_id`, `kind`, `status`,
`display_value` and the derivation metadata.

## 3. Question-Intent Routing

`classify_fyjc_question` resolves the **explicit request** (`Calculate the
Commission`, `Find the Sales`, `Find the missing figure: Sales`) rather than
inferring the target from whichever number appears first. The
Sprint 15 (Stage 4) routing layer, the derivation gate and the C++ solve all
share the same canonical concept spelling, so `Requested: Expenses` shown to
the student matches the concept used by the reasoning and execution layers.

## 4. C++ Authority Invariants

- Every resolved numerical result carries `formula_id != None` and
  `authority_state == "cpp"`.
- The **only** arithmetic authority is the compiled C++ engine; Python
  performs orchestration, validation and display-unit conversion only.
- `DERIVED`/`VERIFIED` with `formula_id=None` is impossible by construction.
- A supplied fact is **never** echoed as a calculated answer: a `direct`
  solve with no registered derivation is `BLOCKED`; a supplied value that
  conflicts with a registered derivation is `REVIEW_REQUIRED` (with the
  reported value preserved in the reason text, never in `display_value`).

## 5. Refusal Boundaries

| Condition | Outcome |
|---|---|
| Missing dependency | `BLOCKED` (names the missing inputs) |
| Genuinely ambiguous wording / intent | `REVIEW_REQUIRED` |
| Topic outside the registered capability boundary (SI, CI, Dividend, GST, AP/GP) | `UNSUPPORTED` |
| Ambiguous numeric formatting (European `1.234,56`) | `BLOCKED` (never guessed) |

The engine never fabricates a value, silently substitutes a dependency,
assumes an unstated percentage, assumes cash/credit when ambiguous, creates
an unvalidated formula, labels an echoed input as a calculated answer, or
bypasses the C++ authority.

## 6. Book-Keeping Rule Composition

Equivalent textbook wordings normalize to the **same** rule IR
(`reason_bk_question`), and the multi-step discount pipeline records every
intermediate stage with a `calculation_id`, formula text, inputs and result:

```
BK_LIST_PRICE -> BK_TRADE_DISCOUNT_AMOUNT -> BK_NET_TRANSACTION_VALUE
  -> BK_PAID_CREDIT_SPLIT -> BK_CASH_DISCOUNT_AMOUNT -> BK_CASH_PAID_NET
```

The composed journal (e.g. `Purchases Dr 9,000 / Cash 4,410 + Discount
Received 90 + Rahul 4,500 Cr`) is deterministic and balanced; ledger and
trial-balance effects derive from the journal IR - never a reinterpretation.

## 7. Coverage Boundaries (unchanged oracles)

The 40-question pilot oracle is preserved: Simple Interest, Compound
Interest, Dividend, GST, AP and GP remain `UNSUPPORTED`. Sprint 15D adds
Commercial Arithmetic (Commission, Trade/Cash Discount, Net Price, Cash
Paid, Creditor/Debtor Balance, Selling/Cost Price, Profit/Loss Percent) and
reverse/inverse paths for the existing ratio relationships - none of which
collide with the pilot oracle.

## 8. Verification

`scripts/fte_fyjc_15d_test.py` is the Sprint 15D gate:

- **Part A** - one canonical formula generates multiple validated paths;
  invalid derivations and impossible dependency sets are refused.
- **Part B** - the coverage benchmark (independent oracles, never calling
  the solver): forward/reverse Commercial Arithmetic, wording variations,
  missing information, ambiguous and unsupported questions.
- **Part C** - Book-Keeping rule composition and the full discount-chain
  audit trail.
- **Part D** - C++ authority + hard invariants (0 unsafe confident answers,
  0 fabricated values, 0 invented accounts, 0 silent substitutions,
  0 `formula_id=None` confident results, 0 C++ authority violations,
  0 unvalidated derived formulas, deterministic repeatability).

The full 14-suite regression matrix (15C P0, 15B, Stage 4 routing, 40-question
pilot, 13 readiness, 14 UI + AppTest, 12A-12F core, Formula Engine Python +
C++ bridge, C++ self-test, student workspace, AppTest/Demo,
py_compile/compileall, `git diff --check`) stays green.
