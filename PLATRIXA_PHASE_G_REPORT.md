# PLATRIXA — PHASE G REPORT
## Financial Semantic Market + Capability + Dataset Architecture

**Date:** 2026-09-19 · **Status:** boundary-definition checkpoint (no code changes to frozen authorities) · **Commit context:** Phases A–F frozen at `1ed73c5`

---

## 1. Executive summary

Phase G tested the working hypothesis — *"Platrixa is financial semantic validation infrastructure for developers building AI-powered accounting and finance software"* — against market categories, input types, event families, failure modes, and the locked 18-field contract.

**Verdict: the hypothesis is confirmed with one narrowing.** The evidence supports Platrixa as event-level semantic validation for transactional financial text (bank narrations, journal descriptions, document text, webhook payloads) with deterministic executability gating. Two adjacent layers are explicitly NOT Platrixa's: document-layout extraction (OCR vendors) and multi-document matching engines (workflow products). The strongest measured capability (payment/receipt narration interpretation + 852-check refusal discipline) aligns with the highest-frequency developer problem (bank-feed categorization and AI bookkeeping posting).

Machine-readable artifacts live in `docs/phase_g/` (6 files). This report is the canonical narrative.

## 2. Current Platrixa architecture (frozen at A–F)

```
Model (untrusted) → 18-field CandidateSemanticIR (LOCKED)
  → schema validation → grounding
  → routing:
      ACCOUNTING KERNEL      9 SUPPORTED · 2 UNSUPPORTED · 1 PLANNED   (deterministic postings)
      FORMULA AUTHORITY     30 SUPPORTED                               (C++-backed, parity 37=37)
      FINANCE_KNOWLEDGE      2 SUPPORTED (27 verified records)         (registry-only, no execution)
```

Boundaries held: knowledge cannot calculate/post/execute; formulas fail closed; kernel is the only posting authority; model output is never authority. 18-field contract: 7 canonical (`transaction_type`, `parties`, `amounts`, `payment_method`, `references`, `ambiguities`, `grounding`) + 11 expanded, with locked enums (15 transaction types, 7 payment methods, 5 ambiguity flags).

## 3. Market/developer use-case map

Full map with evidence in `docs/phase_g/market_use_cases.json` (14 categories). Summary:

| Category | Scope | Basis |
|---|---|---|
| Bank transaction categorization | **CORE** | existing corpus strength + refusal discipline |
| Bookkeeping AI / accounting copilots | **CORE** | kernel journal round-trip verified |
| Financial data normalization (row-wise) | **CORE** | defensive taxonomy maps directly |
| Finance agents (as deterministic gate) | **CORE** | "AI understands; authorities execute" is the product |
| AP automation | FUTURE (events) / CORE (event layer) | bills authority exists; three-way match needs relationship infra |
| AR automation | FUTURE (matching) / CORE (settlement events) | settlement kernel SUPPORTED; partial-settlement abstention pinned |
| Reconciliation tools | ADJACENT (row-level CORE, matching engine OUT) | discrepancy authority exists; matching engine is another product |
| Invoice/document AI | ADJACENT (downstream validator) | Platrixa is not an OCR engine |
| FP&A / CFO analytics | ADJACENT (Formula Authority vectors) | 18-field IR is event-level; statements are states |
| Tax workflow automation | ADJACENT | basic GST SUPPORTED; component cases refused |
| Payment/settlement systems | ADJACENT (event validation only) | idempotency/duplicate semantics defined |
| Financial reporting systems | OUT_OF_SCOPE | trial balances are states, not events |
| Accounting integrations | OUT_OF_SCOPE | provider adapters ≠ semantic validation |

## 4. Core customer hypothesis

**Initial developer customer:** a small team (startup or internal platform team) building AI-assisted bookkeeping or bank-feed features for Indian small-business accounting, who already has an LLM in the loop and needs its financial interpretation to be *deterministically safe* before anything posts to books.

**What they are building:** auto-categorization, NL-to-journal, transaction Q&A, agent actions gated on financial truth.

**The recurring problem across those products:** the LLM produces *plausible but wrong* financial interpretations — invented references, guessed payment modes, silently resolved ambiguity — and there is no deterministic layer that says "this interpretation is grounded, complete, and executable" or "REVIEW_REQUIRED/UNSUPPORTED." Extraction accuracy is a solved-ish problem; **semantic validation with fail-closed abstention is not**.

**Why infrastructure-worthy:** every product in the CORE/ADJACENT list needs the same gate; none wants to build 852 refusal checks, a deterministic kernel, provenance, and quota-metered APIs themselves.

**What belongs to Platrixa:** event-level interpretation → 18-field IR → validation → authority routing → verified/REVIEW_REQUIRED/UNSUPPORTED verdicts.
**What does NOT:** OCR, document layout, matching engines, ERPs, dashboards, forecasting, tax filing.

## 5. Financial input taxonomy

`docs/phase_g/financial_input_taxonomy.json` — 20 input types. SUPPORTED today: bank narration, journal description, credit note (kernel pattern). CANDIDATE (Phase H families): invoice, bill, receipt/OCR text, payment confirmation, bank statement rows, POs, webhooks, email text. OUT_OF_SCOPE: trial balances, financial statements (as IR inputs), accounting-system records, market data. OCR is treated as *noise classes on text*, never as an engine Platrixa owns.

## 6. Financial event ontology

`docs/phase_g/financial_event_ontology.json` — 25 event families (11 with measured gaps) mapped to the **locked** contract enums, current kernel/formula/knowledge coverage, and explicit gaps. Measured baseline failure: the existing corpus is payment-skewed (2,669 of ~3,598 tagged rows are payment-family) — Phase H must *rebalance*, not expand the dominant family. Gap families (loan, payroll, FX, depreciation/accrual, investment, interest-on-loan) enter the dataset **as abstention/REVIEW_REQUIRED examples**, never as improvised postings. Family ≠ contract enum: metadata carries the family; the IR carries the closest locked enum value.

## 7. Cross-document relationship taxonomy

`docs/phase_g/relationship_taxonomy.json` — 9 relationships (`same_event`, `duplicate_of`, `references`, `settles`, `fulfills`, `supports`, `contradicts`, `derived_from`, `supersedes`) each with definition, directionality, evidence required, ambiguity/conflict cases, dataset role, and future-infrastructure note. **No graph implemented in Phase G.** Relationships live in dataset metadata; the IR carries only what its locked fields already represent (`references`, `referenced_*`, `ambiguity_flags`).

## 8. Failure/defensive taxonomy

`docs/phase_g/defensive_taxonomy.json` — 20 classes (normal → model-invention traps), each with expected behavior (EXTRACT / PRESERVE_UNKNOWN / IDENTIFY_CONFLICT / REVIEW_REQUIRED / BLOCKED / UNSUPPORTED), field pressure, and authority outcome. The adversarial class already has a pinned corpus (`platrixa_ai_candidate_cases.jsonl`). Governing rule: **never train the model to invent an answer merely because a target field exists.**

## 9. Exact 18-field coverage matrix

`docs/phase_g/18_field_coverage_matrix.json` — field × input-type matrix with context sensitivity and defensive priority. Findings: well-covered (transaction_type, parties, amounts, payment_method, grounding on clean inputs); under-covered (references from free text, referenced_* across multi-event inputs, confidence calibration on messy inputs); CRITICAL defensive priorities (amounts never guessed, references never invented, ambiguity_flags as the abstention channel, suggested_status REVIEW_REQUIRED discipline). Fields that must NOT populate from certain inputs are explicitly listed.

## 10. Authority coverage map

Mapped per event family in the ontology artifact: 14 families fully SUPPORTED; 11 carry measured gaps — PARTIAL sub-capabilities (interest, deposits, GST components), PLANNED families (depreciation/accrual via ADJUSTMENT_AUTHORITY), and UNSUPPORTED families (FX, payroll, loan, investment, interest-on-loan). Principle enforced: *model understands ≠ Platrixa can execute* — gap families produce abstention examples in Phase H, sharpened by paired supported-neighbor examples.

## 11–12. Dataset-unit architecture + metadata schema

`docs/phase_g/dataset_architecture.json`. One unit = **one financial situation** (not one document): raw_input → source context → interpretation → exact 18-field IR → evidence → relationships → authority check → expected outcome. Metadata schema (`example_id`, `source_type`, `event_family`, `expected_status`, `split`, `leakage_group`, plus optional jurisdiction/framework/difficulty/ambiguity_class/conflict_class/document_relationships/evidence_refs/authority_dependency/generator_lineage) is **strictly separate from the production IR** and reuses the capability registry's status vocabulary — no duplicated status semantics.

## 13. Dataset mixture specification

Dimension-based guardrails (not arbitrary percentages), the key ones: no event family >35% (fixes the measured 74% payment skew); ≥25% of examples carry defensive classes; ≥15% expect non-EXTRACT outcomes; 5–10% carry relationship metadata; every SUPPORTED capability has positive examples and every UNSUPPORTED/PLANNED capability has abstention examples (the capability registry *is* the coverage checklist). The unit+schema scale 10K→100K→1M+ without ontology change.

## 14. Explicit non-goals

Platrixa will NOT own: finance education, generic finance chatbot, PDF/OCR engines, full ERP, bookkeeping UI, complete CFO agent, forecasting platform, generic financial database, payment processing, universal financial data provision. Rejected on architectural fit (event-semantic contract) and evidence (these are owned layers with entrenched products), not because they are merely adjacent.

## 15. Competitive/alternative landscape

Evidence-based layer analysis: document-AI/OCR vendors own extraction (they do not own deterministic validation of the *semantics* — their failure mode is plausible-but-wrong values); workflow AP/AR products own matching engines (they consume semantic validation); accounting platforms own ledgers/UIs; formula/stat libraries own computation (they do not gate *which* facts may enter); LLM vendors own interpretation (they explicitly do not own deterministic financial truth — that is the gap Platrixa occupies). No claim is made that any competitor *cannot* build validation; the differentiation is the verified fail-closed authority stack already implemented (A–F) and its refusal discipline.

## 16. Evidence/source register

- 18-field contract + enums: `backend/maths/schema_verifier.py` (measured, this repo)
- Dataset skew: input-level scan of `training_data/*.jsonl` (2,669 payment-family / ~3,598 tagged rows; measured 2026-09-19)
- Kernel capability boundaries: Phase C registry + boundary-closure suite (852 checks, passing)
- Reconciliation difference classes: Wikipedia, "Bank reconciliation" (timing / unrecorded / errors), retrieved 2026-09-19
- Accounting-software market structure: Wikipedia, "Comparison of accounting software", retrieved 2026-09-19
- IAS 1/7/37, Basel risk families: verified in Phase F (retrieved 2026-09-19)
- Rate-limited/blocked sources (OECD 403, Investor.gov 403, MCA 403): not cited; no claim depends on them

## 17. Open questions

1. Does the initial customer want IR *only*, or IR + executability verdict in one response? (API-shape question, not contract change)
2. Pricing/positioning vs. "just prompt harder" — needs one real pilot conversation.
3. Bank-narration abbreviation coverage: which banks' narration formats to prioritize for Phase H.
4. Multi-event sentence handling: split-IR (future infra) vs REVIEW_REQUIRED-only (current) — Phase H teaches the latter.
5. Whether `derived_from` (FX) abstention examples need a dedicated ambiguity flag or fit `MULTIPLE_INTERPRETATIONS`.

## 18. Recommended Phase H scope

1. Dataset generator for the unit+metadata schema (template-driven, lineage-recorded, leakage-grouped)
2. First batch: ~2–5K examples rebalancing event families to the guardrails, with all 20 defensive classes represented and ≥15% abstention outcomes
3. Coverage checklist derived programmatically from `capability_registry` (SUPPORTED→positive examples; UNSUPPORTED/PLANNED→abstention examples)
4. Validation harness: schema/enum conformance, leakage checks, family-balance guardrails, abstention-rate checks
5. Training-data only — no production code changes

## 19. What Phase H must NOT do

Add fields/enums to the 18-field IR · generate before defensive classes exist · let payment family dominate again · create paraphrase leakage across splits · put metadata inside the production IR · touch kernel/formula/knowledge authorities · implement graphs/OCR/endpoints · upload anything to Hugging Face.

## 20. Final decision gate

Phase G answers all 15 gate questions with evidence: customer (§4), problem (§4), boundary (§4, §14), inputs (§5), events (§6), relationships (§7), failure modes (§8), 18-field mapping (§9), executable-vs-not (§10), Phase H contents/exclusions (§18–19). **Gate: PASSED — Phase H may proceed on this specification.**

---

*Phase G is a specification. No dataset was generated, no model trained, no frozen artifact touched, no authority modified.*
