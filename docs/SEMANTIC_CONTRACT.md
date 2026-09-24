# Platrixa Semantic Contract

The developer-facing explanation of Platrixa's semantic contract, as
actually implemented. Authoritative sources (this document only describes
what those modules define — they remain the source of truth):

- `backend/maths/fyjc_contract.py` — the 18-field contract + enum vocabularies
- `backend/maths/schema_verifier.py` — fail-closed validation of model output
- `backend/semantics/ir.py` — CandidateSemanticIR / GroundedSemanticIR authority boundary
- `backend/kernel/kernel.py` — runtime status vocabulary and result states

## 1. Purpose

Platrixa's language model produces a **structured interpretation** of
financial input. That interpretation is a *candidate* — never financial
truth. The runtime pipeline is:

```
model interpretation
   ↓
CandidateSemanticIR            (the 18-field candidate — what the model believes)
   ↓
schema verification            (strict, fail-closed, no auto-repair)
   ↓
grounding                      (deterministic verification against the source input)
   ↓
GroundedSemanticIR             (only grounding-verified claims)
   ↓
deterministic authority        (decides and executes supported financial results)
   ↓
VERIFIED / REVIEW_REQUIRED / BLOCKED / failure states + evidence
```

Two representations with deliberately different authority:

- **CandidateSemanticIR** — what the model *believes* the input means.
  Frozen, validated view over the model's 18-field candidate. It has **no
  authority over financial truth** and there is deliberately **no path from
  a candidate to accounting**.
- **GroundedSemanticIR** — only the claims that deterministic grounding
  established as supported by the source input. It is the **sole admission
  representation** into deterministic accounting, and it can only be
  constructed through `GroundedSemanticIR.from_candidate(...)` with a
  **passing** deterministic grounding result.

Deterministic authorities — not the model — decide and execute supported
financial results. The model's `suggested_status` is evidence only and is
never the runtime's decision.

## 2. The exact 18-field contract

The candidate contract is exactly these 18 fields (legacy 7 + expanded 11;
source: `fyjc_contract.ALL_VALID_FIELDS` and the
`StructuredInterpretationValidator` type contract). Records with unknown
fields are rejected; nothing is auto-repaired.

### Semantic fields (fields 1–7 — consumed by grounding/accounting)

| # | Field | Meaning | Type/shape |
|---|---|---|---|
| 1 | `transaction_type` | Free-text transaction classification | `str` |
| 2 | `parties` | Parties named in the transaction | `list[str]` |
| 3 | `amounts` | Amount entries (e.g. value, currency, source) | `list[dict[str, str]]` |
| 4 | `payment_method` | Free-text payment mode | `str` |
| 5 | `references` | References to other transactions/entities | `list[str]` |
| 6 | `ambiguities` | Human-readable ambiguity notes | `list[str]` |
| 7 | `grounding` | Model's grounding claims (e.g. `all_fields_explicitly_grounded`, `inferred_fields`) | `dict` |

### Classification / confidence / evidence fields (fields 8–18)

These are part of the 18-field candidate, but they are **metadata, not
additional semantic fields** — they carry classification, confidence, and
evidence information. None of them grants authority: `suggested_status` in
particular is the model's suggestion and is overridden by the deterministic
runtime.

| # | Field | Meaning | Type/shape | Value constraint |
|---|---|---|---|---|
| 8 | `transaction_type_enum` | Validated transaction classification | `str` | `TransactionTypeEnum` (§3) |
| 9 | `payment_method_enum` | Validated payment-mode classification | `str` | `PaymentMethodEnum` (§3) |
| 10 | `ambiguity_flags` | Structured ambiguity classification | `list[str]` | `AmbiguityTypeEnum` (§3) |
| 11 | `referenced_transaction_index` | Index of a referenced prior transaction, if any | `int` or `null` | — |
| 12 | `referenced_party` | Party referenced from a prior transaction, if any | `str` or `null` | — |
| 13 | `referenced_amount` | Amount referenced from a prior transaction, if any | `str` or `null` | — |
| 14 | `field_confidences` | Per-field confidence + grounding records | `list[dict]` | each record requires `field_name`; `confidence` (if present) is a decimal string 0.0–1.0; `grounding` (if present) uses `GroundingLevel` |
| 15 | `overall_confidence` | Overall model confidence | `str` | decimal string 0.0–1.0 |
| 16 | `suggested_status` | Model's suggested status — **evidence only, never authority** | `str` | — |
| 17 | `safety_flags` | Grounding-gate safety check flags | `list[str]` | `SafetyFlag` (§3) |
| 18 | `scope_flags` | Problem classification tags | `list[str]` | `ScopeFlag` (§3) |

**What is NOT part of the contract:** any other key is rejected
(`UNKNOWN_FIELD`). Accounting-truth keys in particular — `journal`,
`journal_entry`, `debit_lines`, `credit_lines`, `ledger`, `balances`,
`debit_account`, `credit_account` — are structurally forbidden in a
candidate; a CandidateSemanticIR cannot even be constructed carrying them.
Dataset/training metadata is also never part of the production contract.

## 3. Enum vocabularies

Exact allowed values (source: `fyjc_contract.py` enums, mirrored by the
validator's `VALID_*` sets):

**TransactionTypeEnum** (15 values)
`PURCHASE`, `SALE`, `PAYMENT`, `RECEIPT`, `CAPITAL`, `EXPENSE`,
`RETURN_OUT`, `RETURN_IN`, `DISCOUNT_TRADE`, `DISCOUNT_CASH`,
`SETTLEMENT`, `GST`, `DRAWING`, `DEPRECIATION`, `UNKNOWN`

**PaymentMethodEnum** (7 values)
`CASH`, `BANK`, `CHEQUE`, `NEFT`, `UPI`, `CREDIT`, `UNKNOWN`

**AmbiguityTypeEnum** (9 values)
`MISSING_PAYMENT_MODE`, `MISSING_AMOUNT`, `MISSING_PARTY`,
`AMBIGUOUS_REFERENCE`, `MULTIPLE_INTERPRETATIONS`,
`CONFLICTING_INFORMATION`, `UNRESOLVED_PRONOUN`, `HISTORICAL_DEPENDENCY`,
`NONE`

**GroundingLevel** (4 values — per-field grounding status, including
`field_confidences[*].grounding`)
`GROUNDED`, `INFERRED`, `UNRESOLVED`, `CONFLICTING`

**SafetyFlag** (9 values)
`AI_CLAIMED_VERIFIED`, `JOURNAL_ENTRIES_PRODUCED`,
`LEDGER_BALANCES_PRODUCED`, `MISSING_REQUIRED_FIELDS`, `LOW_CONFIDENCE`,
`UNRESOLVED_FIELDS`, `AMBIGUITY_DETECTED`, `EMPTY_PARTIES`, `NONE`

**ScopeFlag** (10 values)
`SINGLE_TRANSACTION`, `MULTI_TRANSACTION`, `SINGLE_AUTHORITY`,
`MULTI_AUTHORITY`, `GST_SPECIFIC`, `SETTLEMENT_CALCULATION`,
`RETURN_PROCESSING`, `DISCOUNT_APPLICATION`, `EDGE_CASE`, `ADVERSARIAL`

## 4. Candidate vs Grounded

The boundary (authority: `backend/semantics/ir.py`):

- **CandidateSemanticIR is not authorized to execute accounting.** It is a
  frozen, validated view of the model's proposal. Passing a candidate where
  a grounded IR is required fails loudly rather than silently.
- **GroundedSemanticIR is the admission representation** used after schema
  verification and grounding succeed. Its only constructor is
  `GroundedSemanticIR.from_candidate(candidate, grounding_result)`, which
  requires a **passing** deterministic grounding result
  (`safe_for_kernel` + `grounded`); anything else raises
  `SemanticIRError`.
- **Accounting executes only after admission succeeds.**
  `GroundedSemanticIR.for_accounting()` is the only accounting admission
  contract: it exposes exactly the grounded semantic fields plus the source
  text — never the ungrounded candidate.

## 5. Status vocabulary

Runtime states (source: `backend/kernel/kernel.py`):

| Status | Meaning |
|---|---|
| `VERIFIED` | deterministic authority executed the supported result; the only success state |
| `REVIEW_REQUIRED` | valid interpretation, flagged for human review (e.g. ambiguity, missing information) |
| `BLOCKED` | rejected by the deterministic safety/rule boundary |
| `UNSUPPORTED_TRANSACTION` | the interpreted semantics are outside the currently supported transaction set (fail-closed rejection state) |

Other fail-closed states include `MODEL_UNAVAILABLE`, `VALIDATION_FAILED`,
`GROUNDING_FAILED`, and `FORBIDDEN_OUTPUT`.

**Similarly named internal concepts — not interchangeable runtime
statuses:**

- **maths-internal `UNSUPPORTED`** — a sufficiency state inside the
  deterministic maths engine (unregistered/insufficient concepts); it never
  surfaces as a `KernelResult`/API status.
- **accounting-authority `NOT_SUPPORTED`** — the accounting flow's internal
  vocabulary for outcomes it does not support; the kernel maps accounting
  results into the kernel-level states above.
- **kernel/API-level `UNSUPPORTED_TRANSACTION`** — the actual runtime/API
  status for out-of-supported-set semantics.

There is no bare `UNSUPPORTED` status in `KernelResult` or the API
response.

## 6. Scope note

This document describes the semantic contract currently exposed through
Platrixa's developer surfaces (public interface and `/v1/process`). It does
not imply that every internal authority is exposed through `/v1/process` —
see the README for the current developer exposure of each runtime
authority.
