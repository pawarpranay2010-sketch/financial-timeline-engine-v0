# Platrixa — Financial Document Understanding / OCR Technical Audit

**Status:** read-only audit. No production code, schema, authority, kernel
behavior, model weight, or frozen dataset was modified.
**Date:** 2026-09-24
**Question:** can Platrixa use an existing OCR/document-understanding system
as its document-to-financial-semantics translator, or must it build one?

---

## Answer first

**Use an existing OSS engine as the optical layer. Build only a thin,
Platrixa-specific *evidence adapter* — not an OCR engine and not a document
reasoner.**

The translator role splits cleanly into two parts with different owners:

| Part | Owner | Status |
|---|---|---|
| pixels → glyphs, layout regions, tables, bboxes | **Existing OSS engine** (PaddleOCR / PP-OCRv5 via RapidOCR, or Tesseract) | Buy/compose — do not build |
| glyphs + layout + confidence → Platrixa's `raw_input: str` + a *provable* evidence envelope, with fail-closed rules | **Thin Platrixa adapter** (~1 module, no ML) | Genuine gap — must be built |

The missing layer is *not* OCR. It is the **provable-evidence + fail-closed
boundary** that makes an OCR engine safe to plug into a system whose entire
value proposition is that it never invents a digit. No off-the-shelf
document-understanding product supplies that contract.

---

## 1. What already exists in the repository

### 1.1 Reusable, kernel-disconnected document stack (all present, all working)

| Component | Size | Role | Connected to kernel? |
|---|---|---|---|
| `ingestion/parser.py` | 411 L | PDF/DOCX/XLSX/CSV/TXT/HTML → `{type, pages, tables, table_data, xbrl_facts, text}`; emits `========== PAGE N ==========` markers | **No** |
| `backend/extraction2/table_extractor.py` | 500 L | native tables first, then layout-aware text-table recovery with period/currency/scale detection | **No** |
| `backend/extraction2/financial_extractor_v2.py` | 1118 L | parsed doc → fact graph (XBRL → tables → contextual), evidence hashes, source tiers | **No** |
| `backend/extraction2/document_type_detector.py` | 282 L | invoice/statement/report classification | **No** |
| `backend/extraction2/confidence_scorer.py`, `negative_detector.py` | — | per-fact confidence, negative-evidence detection | **No** |
| `backend/layout_extractor.py` | 504 L | page/table/row/column/period/evidence enrichment, bbox policy, `pages_without_text` | **No** |
| `backend/ocr_verifier.py` | 135 L | **cross-document** conflict detection only — it verifies one figure against *other documents*, it does not do OCR | **No** |
| `backend/confidence_engine.py` / `_v2.py`, `quality_score.py`, `extraction_reliability.py` | — | confidence + reliability scoring | **No** |
| `backend/module3_controller.py` | 303 L | `run_module3(text, extracted_documents)` wires the above together | **No** |

**This is a substantial asset.** The document-understanding *scaffolding* —
page markers, table recovery, evidence enrichment, confidence, fail-closed
"no OCR bundled" policy — already exists. It is simply on a parallel track
from the kernel.

### 1.2 The exact connection point

```
backend/kernel/kernel.py:316   def process(self, raw_input: str, ...) -> KernelResult
```

The kernel's input boundary is **text-only**. The existing document stack
already produces exactly what that boundary wants:

```
ingestion.parser.parse_document(file)
      → parsed["text"]   (with ====== PAGE N ====== markers, 603–13,840 chars measured)
backend/fyjc_student_ui.py:200  _extract_document_text()   ← the existing call site
      → raw_input: str
      → Kernel.process(raw_input)  → schema → grounding → authorities
```

The connection is therefore **one function, not a rewrite**.

### 1.3 The kernel-side hook that already encodes the right policy

`backend/maths/fyjc_bk_15h.py` (spec 6) is a deterministic
Good / Uncertain / Unusable gate over OCR/extraction signals, with
`EXTRACTION_FAILURE` in its failure taxonomy. Its contract is exactly the
policy the new layer must respect: *"a flagged unreadable digit NEVER
produces a parsed amount."* It is the right destination for OCR confidence
signals.

### 1.4 Verified gap: the fail-closed OCR flag is currently dead code

`layout_extractor.py` documents *"pages with no extractable text
(scanned/image pages) → fail closed"* via `pages_without_text`. Measured
against a real image-only PDF, that list comes back **empty**:

| Image-only PDF | pages | chars extracted | facts | `pages_without_text` |
|---|---|---|---|---|
| 1-page scanned invoice | 1 | 0 | 0 | `[]` |
| 5-page scanned bank statement | 5 | 0 | 0 | `[]` |

Cause: `parse_pdf` only emits a `========== PAGE N ==========` marker when
`page.extract_text()` is truthy. A scanned page therefore produces *no
marker at all*, so `_page_lines()` never learns the page existed.

**The outcome is still fail-closed** (zero text → zero facts → nothing can be
grounded), so this is not a correctness hole. But the layer has no way to say
*"page 3 is a scan and needs OCR"* — the signal a real OCR adapter must key
off. Fixing this page accounting is a prerequisite for step 1 below.

---

## 2. Existing open-source / pretrained options

No OCR engine is installed in this environment (no tesseract, no
pytesseract), so **every OCR and VLM number in this report is an estimate**,
labelled as such and sourced. pypdf / Platrixa-chain numbers in §5 are
**measured**.

| Option | License | Financial-doc fit | Multilingual | Tables | Scans | CPU / GPU | Bbox evidence | Platrixa integration |
|---|---|---|---|---|---|---|---|---|
| **pypdf** (already in repo) | BSD-3 | text layer only | n/a | none | **no** | CPU, trivial | no | **done** |
| **PP-OCRv5 / PaddleOCR 3.x** | Apache-2.0 | strong (report benchmark: +4.6% det, +5.1% rec vs PP-OCRv5_server, claims beating much larger VLMs) | 80+ langs, strong CJK/Indic | PP-StructureV3 | yes | CPU ok; ~370 chars/s on a Xeon for mobile rec (published) | **yes, native** | **best fit** — Python-native, Apache, returns text+bbox+confidence in one call |
| **RapidOCR** (ONNX PP-OCRv5) | Apache-2.0 | same models, no PaddlePaddle runtime | same | via PP-Structure | yes | CPU-only ONNX, light RAM | yes | best *deployment* form of the above |
| **Tesseract 5** | Apache-2.0 | adequate for clean printed text only | 100+ langs | weak | yes | CPU-only, no GPU | hOCR/TSV | fallback only — no layout model, confuses dense tables |
| **EasyOCR** | Apache-2.0 | middling | 80+ | weak | yes | CPU ok | yes | redundant with PaddleOCR, worse accuracy |
| **Docling** | MIT | good pipeline glue | via OCR backend | good | via backend | CPU ok | partial | useful orchestration, but heavier |
| **olmOCR / MinerU / Marker / Surya** | Apache-2.0 / **AGPL-3.0** / **GPL-3.0 + CC-BY-NC-SA weights** / **GPL-3.0** | VLM-class accuracy | yes | excellent | yes | **GPU-class** (7B+ VLM) | partial | **excluded on licensing and hardware** |
| **Azure DI / Google Doc AI / Textract** | SaaS | excellent prebuilt invoice/statement models | yes | excellent | yes | cloud | yes, polygons | viable, but external dependency + per-page cost; see §8 |

**Recommendation on the engine: RapidOCR/ONNX running PP-OCRv5
(Apache-2.0), Tesseract 5 as the always-available fallback.**

Not because it is popular — because it is the only candidate that is
simultaneously (a) Apache-2.0 for both code and weights, so it carries no
copyleft or non-commercial obligation, (b) CPU-viable for this project's
2-core sandbox, (c) multilingual including Devanagari, which matters for an
Indian-accounting product, and (d) returns **bounding boxes and per-region
confidence natively** — the two things the evidence adapter needs and the
thing Tesseract does *not* do well. PP-StructureV3 handles the table
segment later without changing the engine.

The AGPL/GPL family (MinerU, Marker, Surya) is disqualified on licensing
alone for a hosted service, independent of their better benchmark scores.

---

## 3. The "translator" role and its boundary

Yes, an existing component can realistically be the translator — provided the
contract is drawn at the right line.

```
PDF / image
   ↓
[EXISTING OSS ENGINE]  PP-OCRv5 / Tesseract        ← pixels → glyphs + bboxes + confidence
   ↓
[NEW, THIN — NO ML]    Platrixa DocumentAdapter      ← evidence envelope, page accounting,
   ↓                                                        confidence → Good/Uncertain/Unusable
DocumentRepresentation (text + pages + tables + bboxes + evidence + confidence)
   ↓
[EXISTING] ingestion.parser → raw_input: str
   ↓
[EXISTING] Semantic Interpreter (Qwen2.5-1.5B LoRA) → 18-field CandidateSemanticIR
   ↓
[EXISTING] Schema Verifier (strict, fail-closed)
   ↓
[EXISTING] Grounding Gate (fail-closed)  ← hallucinated values die here (proved in §5.3)
   ↓
[EXISTING] Capability / Authority routing → ACCOUNTING_KERNEL / FORMULA_AUTHORITY / FINANCE_KNOWLEDGE
   ↓
VERIFIED / REVIEW_REQUIRED / BLOCKED
```

**The boundary rule, stated once: OCR may propose *text and coordinates*.
Only the deterministic runtime may propose a financial conclusion.**

Concretely, the OCR layer must never:
- compute totals, net amounts, taxes, or any arithmetic;
- emit a status, a `VERIFIED` flag, or an accounting line;
- write to any authority, or be imported by `backend/maths/`;
- feed `FinancialExtractorV2` output into the kernel as trusted input.

The layer *may* and must: preserve page numbers, bboxes, per-region
confidence, the source file name and hash, and the raw recognized string
unmodified.

This is not a new policy — it is the policy already written into
`layout_extractor.py` ("OCR is only a flag here… low-confidence OCR can never
become a Verified fact because no OCR-derived value ever enters this
enrichment") and `fyjc_bk_15h.py` ("a flagged unreadable digit NEVER produces
a parsed amount"). The adapter implements existing intent; it does not
create new authority.

---

## 4. Proposed integration architecture (design only — nothing implemented)

New module, isolated, no ML, no authority imports:

```
backend/document_understanding/
    adapter.py            DocumentAdapter.interpret(file) -> DocumentRepresentation
    representation.py     dataclasses: Page, Block, BBox, TableBlock, EvidenceRef
    engine.py             engine registry: rapidocr | tesseract | none  (pluggable)
    quality.py            confidence -> Good | Uncertain | Unusable  (15H taxonomy)
```

Input interface: `bytes | path | file-like + file_name`, mirroring
`ingestion.parser.parse_document(uploaded_file)`.

Normalized representation:

```python
DocumentRepresentation(
    source_name, source_sha256, page_count,
    pages=[Page(number, width_pt, height_pt, blocks=[...])],
    tables=[TableBlock(page, bbox, rows, source_block_refs)],
    text="\n========== PAGE N ==========\n...",   # byte-compatible with parser output
    regions=[Block(text, page, bbox, confidence, engine, engine_version)],
    quality=Good | Uncertain | Unusable,
    quality_reasons=[...],          # e.g. "page 3 has no OCR engine"
)
```

Handoff: `DocumentRepresentation.text` → `Kernel.process(raw_input: str)`.
`regions[]` is carried alongside for audit/UI citation and for the grounding
gate to resolve "where did this number come from". No kernel signature change
is required, because the kernel is already text-only — that is a property to
exploit, not a limitation to work around.

---

## 5. Processing speed — measured vs estimated

Environment: 2 vCPU, ~3 GB RAM, **no GPU**, Python 3.10, pypdf 6.14.2.
The real 1.5B model could not be downloaded (1.5 GB free disk vs ~3 GB
needed), so model inference is estimated, not measured. Harness:
`audit/_bench_document_pipeline.py`, `audit/_bench_kernel_deterministic.py`.

### 5.1 MEASURED — pypdf text extraction (digital PDFs, warm median, ms)

| Doc | Pages | Cold | Warm | ms/page | Chars | Throughput |
|---|---|---|---|---|---|---|
| Invoice | 1 | 3.3 | **2.7** | 2.7 | 609 | 375 pg/s |
| Bill | 2 | 4.8 | **4.6** | 2.3 | 1,236 | 432 pg/s |
| Bank statement | 5 | 70.2 | **53.8** | 10.8 | 13,840 | 93 pg/s |
| Financial report | 10 | 25.0 | **25.3** | 2.5 | 7,041 | 396 pg/s |

Digital-PDF text extraction is **not a bottleneck** — 2–11 ms/page.

### 5.2 MEASURED — full existing chain (parse → layout → facts, ms)

| Doc | Pages | parse_document | layout | facts (v2) | Chain total | Throughput |
|---|---|---|---|---|---|---|
| Invoice | 1 | 3.8 | 0.08 | 0.81 | **4.7** | 12,700 doc/min |
| Bill | 2 | 6.1 | 0.12 | 1.47 | **7.7** | 7,800 doc/min |
| Bank statement | 5 | 69.0 | 0.91 | 14.57 | **84.5** | 710 doc/min |
| Financial report | 10 | 29.4 | 0.56 | 10.39 | **40.4** | 1,490 doc/min |

Peak RSS across the whole run: **126 MB → 234 MB**. No memory concern.

**Important negative result:** on these realistic synthetic financial
documents, `FinancialExtractorV2` produced **0 facts** for the invoice, bill
and bank statement, and **1 fact** for the 10-page report, while
`TableExtractor` found 1/1/29/4 tables. The fact-graph layer is currently a
weak consumer of good tables. This is independent of OCR and worth its own
follow-up; it does not block the OCR decision.

### 5.3 MEASURED — deterministic kernel (stub provider; LLM excluded, ms)

| Payload | Chars | Cold | Warm | Status decided by runtime |
|---|---|---|---|---|
| 1 sentence | 61 | 46.4 | **1.16** | REVIEW_REQUIRED |
| Invoice text | 403 | 5.2 | **4.43** | REVIEW_REQUIRED |
| 40-row statement | 3,185 | 70.5 | **67.51** | REVIEW_REQUIRED |

Boundary behaviour, verified end-to-end:

| Probe | Result |
|---|---|
| Empty / whitespace input | `VALIDATION_FAILED`, success=False |
| Unreadable-scan placeholder text | `GROUNDING_FAILED`, success=False |
| Ambiguous interpretation | `BLOCKED`, success=False |
| **Hallucinating interpreter** (invented party, amount 999999.99, "cash") | **`GROUNDING_FAILED`** — "Party not supported by input text", "Amount 999999.99 not supported by input text", "Payment method 'cash' not explicitly supported" |

That last row is the load-bearing result: even a maximally unfaithful
interpreter cannot produce a `VERIFIED` outcome. A document layer feeding
this pipeline is structurally safe.

### 5.4 ESTIMATED — OCR / VLM stages (**not measured; no engine available**)

| Stage | CPU (2-core, est.) | GPU (est.) | VRAM | Basis |
|---|---|---|---|---|
| Tesseract 5, per page | 0.8–2.0 s | n/a (CPU-only) | 0 | published invoice-engine roundups |
| PP-OCRv5 mobile, per page | 0.3–1.0 s | 50–150 ms | 0 / ~1 GB | published >370 chars/s Xeon claim |
| PP-StructureV3 (layout+tables), per page | 1–3 s | 0.2–0.5 s | 0 / ~2–4 GB | pipeline overhead over base OCR |
| VLM OCR (olmOCR/MinerU-class, 7B+) | **minutes/page — impractical** | 1–3 s/page | ≥16 GB | 7B+ weights, no CPU path |

### 5.5 ESTIMATED — end-to-end

| Doc | Digital PDF (OCR skipped) | Scanned (OCR added) |
|---|---|---|
| 1-page invoice | **~5 ms** measured chain + LLM | ~1–2 s + LLM |
| 2-page bill | **~8 ms** measured | ~2–4 s + LLM |
| 5-page statement | **~85 ms** measured | ~3–6 s + LLM |
| 10-page report | **~40 ms** measured | ~5–12 s + LLM |

Semantic interpretation is excluded from the measured column because the
model cannot be loaded here. Budget separately per provider (this repo's
existing live-provider runs recorded ~2.8–15 s per LLM call), and prefer
**per-page or per-region interpretation over whole-document**, so a 10-page
statement does not become one 15-second call.

**Resource requirements:** OCR tier ~0.5–1.5 GB RAM CPU-only (RapidOCR ONNX
form) or ~2–4 GB with PP-StructureV3; no VRAM needed. LLM tier unchanged from
today (1.5B LoRA, GPU or provider API).

---

## 6. Does Platrixa need to build anything?

**Yes — one thin adapter. No OCR engine, no layout model, no VLM.**

What must be built (~1 module, no ML, deterministic, testable):

1. **Page accounting fix.** Make `parse_pdf` emit a page marker for *every*
   page, including textless ones, so `pages_without_text` populates. Without
   this the adapter cannot tell a scan from a blank page.
2. **`DocumentRepresentation`** with pages, blocks, bboxes, per-region
   confidence, source hash — the envelope the existing enrichment code
   already half-expects.
3. **Pluggable engine registry** (`rapidocr | tesseract | none`) so the engine
   is swappable and the system degrades to today's behaviour when no engine
   is present.
4. **Quality gate** mapping engine confidence → the 15H
   Good/Uncertain/Unusable states and `EXTRACTION_FAILURE` reasons.
5. **Byte-compatible text serialization** so `Kernel.process(raw_input: str)`
   needs no change at all.

Explicitly **not** to be built: OCR models, table-recognition models,
layout analysis, a VLM, a second schema, a second status vocabulary, or any
financial reasoning. Anything that computes a number belongs to the kernel.

---

## 7. Risks and limitations

1. **OCR digit errors are silent.** A misread `8`→`3` yields a
   well-formed, grounded, *wrong* number. Grounding proves the number came
   from the text; it cannot prove the text matches the pixels. Mitigation:
   the Good/Uncertain/Unusable gate plus a confidence floor before any figure
   is treated as more than REVIEW_REQUIRED. This is the single largest risk
   and the reason the adapter is worth building.
2. **Table→fact weakness (measured).** §5.2 shows the fact-graph layer
   extracting ~0 facts from good tables. OCR will make this *worse* before it
   makes it better, because noisier text feeds the same consumer.
3. **Handwriting and low-quality scans.** PP-OCRv5 improved non-standard
   handwriting error rate, but financial forms with stamps, signatures and
   carbon copies remain the weak case; expect Unusable.
4. **Silent-degradation risk.** If the engine is missing, the naive path
   returns "no facts" indistinguishable from a factless document. The
   `quality_reasons` field exists specifically to make that distinguishable.
5. **Evidence resolution cost.** Cross-referencing a kernel value back to a
   bbox is string matching at present; a 10-page doc makes this the slowest
   deterministic step (67 ms measured at 3.2k chars).
6. **Licensing.** Apache-2.0 (PP-OCRv5/RapidOCR/Tesseract) is safe for hosted
   commercial use. Do **not** adopt MinerU (AGPL-3.0), Marker or Surya
   (GPL-3.0, and Marker/Surya weights are CC-BY-NC-SA — non-commercial)
   without legal review; their benchmark advantage does not outweigh this for
   a hosted product.
7. **No Platrixa test corpus.** All benchmarks above are synthetic. Any
   accuracy claim is premature until real invoices/statements are measured.

---

## 8. Exact next steps

1. **Fix page accounting** in `ingestion/parser.py::parse_pdf` so textless
   pages still emit markers; re-run the scan probe to confirm
   `pages_without_text` populates. (Smallest change, unblocks everything.)
2. **Golden corpus first** — assemble 20–30 real financial documents (invoices,
   bills, bank statements, annual-report pages) spanning digital and scanned,
   single- and multi-column, with expected values. Every later accuracy or
   latency number depends on this existing first.
3. **Build the adapter skeleton** (`backend/document_understanding/`) with
   `engine="none"` as the only registered engine — proves the envelope, page
   accounting and fail-closed path with zero new dependencies.
4. **Add RapidOCR (PP-OCRv5 ONNX) as engine #1**, Tesseract as #2. Measure
   against the corpus; record per-page latency, digit-error rate and bbox
   fidelity. Replace every estimate in §5.4 with a measurement.
5. **Wire the quality gate** to the 15H Good/Uncertain/Unusable taxonomy so
   low-confidence regions cannot reach interpretation as clean text.
6. **Decision gate:** if digit-error rate on the golden corpus is not
   materially below the cost of manual review, stop before shipping
   auto-posting; the layer is still useful for `REVIEW_REQUIRED` triage.
7. Only then expose a document entry point. Do **not** add a second status
   vocabulary or bypass `Kernel.process(raw_input: str)`.

---

## Appendix — audit method

- Repo audit: full read of `ingestion/parser.py`, `backend/layout_extractor.py`,
  `backend/extraction2/*`, `backend/ocr_verifier.py`, `backend/module3_controller.py`,
  `api/main.py` + `api/routes/kernel.py`, `backend/maths/fyjc_bk_15h.py`, and the
  kernel text-only boundary at `backend/kernel/kernel.py:316`.
- Benchmarks: `audit/_bench_document_pipeline.py` (reportlab-built digital and
  image-only PDFs at 1/2/5/10 pages; pypdf, parser, table, layout, fact stages;
  cold + warm median) and `audit/_bench_kernel_deterministic.py` (real kernel,
  stub provider; boundary probes). Both are read-only harnesses.
- Options research: current vendor/project documentation and 2025–2026
  comparative benchmarks. All OCR/VLM figures in §5.4–5.5 are estimates
  because no OCR engine is installed here; all figures in §5.1–5.3 are
  measured on this machine.
- No production file was modified.
