# FT-E Maths Decision Graph — Sprint 12C

Evidence-Aware Decision Graph & Production Integration.

Sprint 12C turns the Sprint 12A deterministic maths engine and the
Sprint 12B contextual reasoning layer into a complete **auditable
financial reasoning graph**. It is additive: no 12A/12B behavior was
changed; the C++ deterministic engine remains the mathematical authority.

---

## 1. Architecture

```
PDF / DOCX / XLSX / CSV / TXT / approved APIs / student input
        ↓
EXTRACTION  (existing extractors)
        ↓
CANONICAL NORMALIZATION  (existing pipeline)
        ↓
PROVENANCE + VERIFICATION
        ↓   evidence.py (4-tier source hierarchy)
        ↓   provenance.py (Provenance Integrity Gate)
UNIT / CURRENCY / PERIOD NORMALIZATION  (units.py, 12A)
        ↓
ACCOUNTING GRAPH  (12A)
        ↓
SUFFICIENCY  (12A)
        ↓
SOLVER  (12A + C++ authority)
        ↓
REASONING LAYER  (12B: DuPont / Reconciliation / Adjustments)
        ↓
DECISION GRAPH  (decision_graph.py)
        ↓
Evidence lineage (evidence.py)  +  Excel compiler (excel_compiler.py)
        ↓
Agent payload  +  Excel Working Model
```

New Sprint 12C modules (all under `backend/maths/`):

| Module | Responsibility |
|---|---|
| `evidence.py` | Evidence model, strict 4-tier source hierarchy, external-evidence records, recursive leaf tracing |
| `provenance.py` | Provenance Integrity Gate (source / tier / document / page / period / currency / unit / evidence checks) |
| `extended_registry.py` | Declarative registry expansion (ROE, ROA, Current Ratio, D/E, margins, EBITDA margin, CAGR, EPS) + formula metadata incl. Excel templates |
| `excel_compiler.py` | Excel lineage compiler (live nested formulas; blocked state preserved) |
| `decision_graph.py` | Deterministic decision layer + `DecisionNode` + agent payload + `evaluate_metric()` |
| `scripts/fte_maths_decision_graph_test.py` | 99-check suite (sections A–Y) |

Only one additive change touches Sprint 12A: `formula_registry.py`
gains a `^` power operator (required so CAGR can be registered
declaratively). All 12A tests remain green.

---

## 2. Graph model

Every fact is a `FactNode` (12A) that keeps its ORIGINAL representation
alongside its normalized value, plus provenance metadata:

- `node_id`, `canonical_concept`
- `value` / `original_value` / `original_unit` / `original_scale`
- `currency`, `period`, `period_type`
- `source`, `source_tier`, `document_name`, `page`, `evidence`
- `status` (six-tier), `excel_cell_coordinate`

A derived metric never exists without a deterministic dependency path:
`solve()` produces a `Solution` whose `LineageRecord` lists every step,
input, intermediate value and traversal path. `trace_leaves()` (12C)
recursively expands that lineage to the terminal **source leaves**
(document / page / evidence / provider), machine-readable:

```
ROE
 ├── Net Profit          -> Document A / page 42 (p&l line)
 └── Equity              -> Document A / page 87 (bs line)
ROE = Net Profit / Equity
```

---

## 3. Formula registry

`formula_registry.py` (12A) + `extended_registry.py` (12C). Registration
is fully separated from application: the solver never contains per-formula
arithmetic. Each formula declares `formula_id`, `target`, `expression`,
`dependencies`, `inverses` (reverse solving), `unit_kind`, `period_mode`,
`denominator_constraints`, `domain_rules`, `version`, `source_ref`.

New in 12C (`EXTENDED_FORMULA_METADATA`, section-8 metadata):

- `expected_input_kinds`, `output_kind`
- `status_requirement` (weakest-link)
- `lineage_behavior` (full)
- `excel_template` (consumed by the Excel compiler)

New formulas (all declarative — no solver change):

| formula_id | expression | kind |
|---|---|---|
| `ROE` | Net Profit / Equity | percent |
| `ROA` | Net Profit / Total Assets | percent |
| `CURRENT_RATIO` | Current Assets / Current Liabilities | ratio |
| `DEBT_TO_EQUITY` | Debt / Equity | ratio |
| `GROSS_MARGIN` | Gross Profit / Revenue | percent |
| `OPERATING_MARGIN` | Operating Profit / Revenue | percent |
| `EBITDA_MARGIN` | EBITDA / Revenue | percent |
| `CAGR` | (Ending / Beginning)^(1/n) − 1 | percent |
| `EPS` | Net Profit / Shares Outstanding | amount |

CAGR uses the `^` power operator and requires an explicit integer span
(years). The span is never guessed: provide `CAGR Span Years` as a fact
or derive it from the two periods with `derive_cagr_span()` (deterministic;
returns `None` when it cannot be established). EPS divides currency by a
share count; per the 12A unit gate, a share count carrying a *classified*
unit (e.g. "shares") is not mixed with currency — such a fact fails
closed (`BLOCKED`) rather than silently combining quantity kinds.

---

## 4. Solver (forward / reverse / chained)

The 12A generic solver is unchanged and supports:

- **Forward**: `Revenue − Expenses → Profit`
- **Reverse**: `Revenue + Profit → Expenses`, `Revenue + Loss → Expenses`,
  `Profit + Expenses → Revenue`, `COGS = Revenue − Gross Profit` — only
  where the registry registers a mathematically valid inverse. Unknown
  relationships → `INSUFFICIENT`/`BLOCKED`; never guessed.
- **Chained**: `Net Profit → Profit Margin → ROE`, DuPont chains, etc.,
  resolved bottom-up along a deterministic topological order with memoized
  sub-results (never cached across different source facts / periods /
  units / currencies / adjustment states).

Arithmetic authority: the compiled C++ engine is consulted for each atomic
step; its result is used only when it reproduces the exact Decimal value
(precision guard). Otherwise the exact Decimal path stands.

---

## 5. Status propagation

Six tiers (12A): `VERIFIED`, `DERIVED`, `RECONCILED`, `STUDENT_INPUT`,
`REVIEW_REQUIRED`, `BLOCKED` — weakest-link propagation:

- any `BLOCKED` dependency → downstream `BLOCKED`
- `REVIEW_REQUIRED` never silently becomes `VERIFIED`/`DERIVED`
- a computed result can never be `VERIFIED`

---

## 6. Provenance integrity gate

`provenance.py` — before a result is marked `VERIFIED` or `DERIVED`,
every source leaf must pass deterministic validation:

1. source exists (document or provider identity)
2. source type / tier is allowed (Tier 1–3; Tier 4 forbidden)
3. document identity exists where required (page-backed docs)
4. page provenance exists for page-backed documents
5. period matches the reference context (where provided)
6. currency matches the reference context (where provided)
7. units / scales are compatible (unknown scales never guessed)
8. source evidence non-empty where required
9. dependency statuses valid (solver fails closed on BLOCKED)

Missing provenance is never fabricated: insufficient → `REVIEW_REQUIRED`;
forbidden/unanalyzable → `BLOCKED`.

---

## 7. Strict source hierarchy

`evidence.py`:

| Tier | Source | Allowed |
|---|---|---|
| 1 | uploaded primary document (`DOCUMENT`) | yes |
| 2 | user-uploaded parent/appendix docs (`APPENDIX`) | yes |
| 3 | approved regulatory / structured APIs (`REGULATORY_API`, `EXTERNAL_DERIVED`) | yes |
| 4 | everything else (`OPEN_WEB`, unknown, scraped …) | **FORBIDDEN** |

The maths engine never silently retrieves from the open web. External
facts enter the graph only through an approved evidence adapter and
become `ExternalEvidenceRecord`s carrying provider, retrieval timestamp,
identifier, source type, period, currency, unit, raw value, normalized
value, evidence metadata and verification status. An external value is
never automatically `VERIFIED` merely because an API returned it.

---

## 8. Reconciliation

`reconciliation.py` (12B) — cross-statement rules (retained-earnings
strap, cash-flow identity, direct two-statement comparisons). Matching
gates run before ANY comparison (explicit periods — never label-matched —
fiscal period type, currency, scale, distinct statements, provenance).
Variance rule: `abs(variance) >= tolerance → REVIEW_REQUIRED` with the
full structured payload; original values are always preserved.

---

## 9. Adjustment reasoning

`adjustments.py` (12B) — detects candidates (`CROSS_STATEMENT_DISCREPANCY`,
`CONFLICTING_SOURCE_VALUES`, `DUPLICATE_FACT`, `INCOMPATIBLE_UNITS`,
`PERIOD_MISMATCH`, `UNEXPECTED_SIGN`, `MISSING_DEPENDENCY`,
`ZERO_DENOMINATOR`, `SCALE_MISMATCH`, `UNSUPPORTED_LABEL`,
`CONFLICTING_PROVENANCE`).

```
VERIFIED source → ANOMALY DETECTED → REVIEW_REQUIRED
                → explicit user/student adjustment → STUDENT_INPUT
                → recalculate graph
```

Original extracted facts are immutable: an adjustment creates a NEW
analytical node whose lineage records the original facts, the anomaly,
the adjustment and the decision. The forbidden
`VERIFIED → automatic adjustment → VERIFIED` flow never happens.

---

## 10. Excel compilation

`excel_compiler.py` — every derived result can produce:

1. human-readable lineage (`render_excel_lineage_text`)
2. an active Excel formula over the Financial Data sheet

```
ROE  ->  ='Financial Data'!E3 / 'Financial Data'!E9
```

Multi-step chains preserve the full nested algebraic chain:

```
ROE = (E3 / E5) * (E5 / E7) * (E7 / E9)      (DuPont, nested)
```

Rules: derived values are never hardcoded when a valid dependency graph
exists; reverse steps compile the REGISTERED inverse expression; missing
coordinates → no formula with an explicit reason (never fabricated);
`BLOCKED` → the Excel cell preserves the blocked state (no fabricated
value); `REVIEW_REQUIRED` / `RECONCILED` / `STUDENT_INPUT` results still
compile the formula but carry their status so the workbook never presents
them as verified. The existing deterministic serialization of
`excel_working_model.py` is untouched.

---

## 11. Decision layer

`decision_graph.py` — `DecisionNode` states:

| Decision | Meaning |
|---|---|
| `METRIC_AVAILABLE` | fact directly supported by evidence |
| `METRIC_DERIVED` | computed through registered formulas |
| `METRIC_RECONCILED` | obtained through a documented reconciliation |
| `METRIC_STUDENT_INPUT` | explicit student-adjusted analytical value |
| `EVIDENCE_CONFLICT` | conflicting sources / ambiguous derivation |
| `RECONCILIATION_REQUIRED` | cross-statement variance needs review |
| `ADJUSTMENT_REQUIRED` | anomaly candidate needs a student decision |
| `METRIC_BLOCKED` | required dependency unavailable / invalid |
| `INSUFFICIENT_EVIDENCE` | no registered relationship can produce it |

`evaluate_metric(target, facts, ...)` orchestrates: fact graph →
provenance gate → solver → anomaly/reconciliation context → decision →
evidence trace → Excel formula. Decisions are pure, deterministic
functions of (solution status/value, provenance verdict, anomaly
candidates, reconciliation results) — no LLM, no advice.

### Agent payload (section 11)

```json
{
  "target": "ROE",
  "status": "DERIVED",
  "decision": "METRIC_DERIVED",
  "value": 40.0,
  "display_value": "40.00%",
  "formula": "Net Profit / Equity",
  "formula_id": "ROE",
  "dependencies": ["Net Profit", "Equity"],
  "lineage": [ ...step dicts... ],
  "evidence": [ ...source-leaf dicts (document/page/evidence)... ],
  "blocking_reason": null,
  "reason": "ROE is calculated deterministically from verified dependencies.",
  "sufficiency_state": "FORWARD_SOLVABLE",
  "excel_formula": "='Financial Data'!E3 / 'Financial Data'!E9"
}
```

---

## 12. Failure states

Every failure becomes a structured deterministic state. Never: `NaN` as a
meaningful result, silent zero substitution, arbitrary source selection,
interpolation, LLM calculation, open-web scraping, or fabricated evidence.

| Condition | Deterministic state |
|---|---|
| missing fact | `BLOCKED`, missing deps named |
| duplicate fact | `DUPLICATE_FACT` candidate |
| conflicting fact | `EVIDENCE_CONFLICT` (both preserved) |
| unsupported label | `UNSUPPORTED_LABEL` candidate |
| unsupported formula | `INSUFFICIENT_EVIDENCE` |
| incompatible units | `BLOCKED` (`UnitMismatchError`) |
| incompatible currencies | `BLOCKED` (never converted) |
| incompatible periods | `BLOCKED` / `REVIEW_REQUIRED` (reconciliation) |
| zero denominator | `BLOCKED` (`DomainError`) + `ZERO_DENOMINATOR` |
| invalid scale | `BLOCKED` (`ScaleMismatchError`) |
| invalid provenance | `METRIC_BLOCKED` ("invalid provenance") |
| circular dependency | `BLOCKED` (circular) / `CycleDetectedError` |
| unresolved reconciliation | `RECONCILIATION_REQUIRED` |
| unresolved adjustment | `ADJUSTMENT_REQUIRED` |
| unavailable external evidence | `BLOCKED` (fail closed) |
| malformed input | `BLOCKED` (never coerced to a number) |

---

## 13. Complete example

PDF → extracted Revenue → normalized Revenue → Profit Margin → Asset
Turnover → Equity Multiplier → DuPont ROE → evidence lineage → Agent
payload → Excel formula.

**Input facts** (from `AR2025.pdf`):

| Concept | Value | Unit | Period | Doc / page | Tier |
|---|---|---|---|---|---|
| Net Profit | 600 | USD | FY2025 | AR2025.pdf / 42 | DOCUMENT |
| Revenue | 3000 | USD | FY2025 | AR2025.pdf / 40 | DOCUMENT |
| Total Assets | 6000 | USD | FY2025 | AR2025.pdf / 87 | DOCUMENT |
| Equity | 2000 | USD | FY2025 | AR2025.pdf / 88 | DOCUMENT |

**DuPont chain** (through `DUPONT_REGISTRY` + Solver):

```
Profit Margin      = Net Profit / Revenue        = 0.200
Asset Turnover     = Revenue / Total Assets      = 0.500
Equity Multiplier  = Total Assets / Equity       = 3.000
Return on Equity   = PM × AT × EM                = 30.00% (percentage number)
```

**Evidence lineage** (machine-readable):

```
leaves: [Net Profit (p.42), Revenue (p.40), Total Assets (p.87), Equity (p.88)]
chain:  Net Profit -> Revenue -> Total Assets -> Equity
        -> Profit Margin -> Asset Turnover -> Equity Multiplier -> Return on Equity
```

**Agent payload**: `{"target": "Return on Equity", "status": "DERIVED",
"decision": "METRIC_DERIVED", "value": 30.0, ...}`.

**Excel formula** (nested algebraic chain over the Financial Data sheet):

```
=('Financial Data'!E3 / 'Financial Data'!E5)
 * ('Financial Data'!E5 / 'Financial Data'!E7)
 * ('Financial Data'!E7 / 'Financial Data'!E9)
```

**Two-period contribution analysis** (12B, sequential replacement):

```
dROE = dPM·AT₁·EM₁ + PM₀·dAT·EM₁ + PM₀·AT₀·dEM   (exact identity)
FY2024 ROE = 20.00%  ->  FY2025 ROE = 30.00%      Δ = +10.00pp
largest contributor: Equity Multiplier
```

---

## 14. Verification

- `scripts/fte_maths_decision_graph_test.py` — 99/99 (sections A–Y,
  determinism runs)
- `scripts/fte_maths_reasoning_test.py` (12B) — 123/123
- `scripts/fte_maths_core_test.py` (12A) — 202/202
- `scripts/fte_formula_engine_test.py` / `fte_formula_engine_cpp_test.py`
  — all checks complete (C++ engine untouched)
- FT-E regression suites — all pass
- `git diff --check` — clean

Final state: **12A PASS, 12B PASS, 12C PASS, 0 new regression failures,
0 fabricated financial values, 0 open-web evidence paths, 0 silent source
substitutions.**
