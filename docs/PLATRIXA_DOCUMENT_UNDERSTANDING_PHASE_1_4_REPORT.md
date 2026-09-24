# Platrixa — Document Understanding Integration, Phases 1–4

**Status:** implemented and verified. Phases 1–4 complete.
**Date:** 2026-09-24
**Commits:** `52e329d` (Phase 1) · `01b3bfc` (Phase 2) · `578618a` (Phase 3) · this commit (Phase 4)

---

## 1. Objective

Let developers submit **text, PDFs, and images** through the existing
developer API and receive the same deterministic, grounded result they get
today from text — with every page accounted for, evidence preserved, and
**no new financial authority**.

---

## 2. Architecture

```
Developer App
   ↓  text  OR  PDF  OR  image
Platrixa API  ── POST /v1/process/document  (multipart or JSON)
   ↓
Document Input Router          (backend/document_understanding/inputs.py)
   │   transport validation only: type, size, exclusivity.
   │   Rejects → 400 / 413 / 415. Never a financial verdict.
   ↓
Document Understanding         (page_accounting.py)
   │   EVERY physical page is represented, always, with an explicit
   │   status: DIGITAL_TEXT | IMAGE_ONLY | EXTRACTION_FAILED
   ↓
OCR (optional)                  (ocr.py · ocr_rapid.py · ocr_tesseract.py · registry.py)
   │   Runs ONLY on pages with no text layer. Behind a replaceable
   │   interface. Absent → those pages simply stay IMAGE_ONLY.
   ↓
Evidence Adapter                (adapter.py · representation.py)
   │   Deterministic. Normalizes content into citable evidence:
   │   document id · page · text · bbox · confidence · source · engine.
   │   NO financial reasoning. NO status. NO fabrication.
   ↓
Document Representation  ── text_for_interpretation()
   ↓  (the EXISTING text-only boundary, page markers included)
Financial Semantic Interpreter
   ↓
EXACT existing 18-field CandidateSemanticIR      (7 legacy + 11 expanded)
   ↓
existing Schema Verification  →  existing ExpandedGroundingGate
   ↓
existing Authority Routing → ACCOUNTING_KERNEL / FORMULA / KNOWLEDGE
   ↓
VERIFIED / REVIEW_REQUIRED / BLOCKED / UNSUPPORTED
```

### The boundary, stated once

| Layer | Question it answers | May it decide financial truth? |
|---|---|---|
| OCR / document understanding | "What is present in the source?" | **No** |
| Semantic interpretation | "What financial meaning does this represent?" | No — it only proposes |
| Grounding | "Is that meaning supported by the source?" | No — it only accepts/rejects |
| Authorities | "What deterministic operation is permitted?" | **Yes — and only they** |

The document path owns **only the first three arrows**. Everything from the
semantic interpreter onward is the pre-existing runtime, reached through its
pre-existing `raw_input: str` entry point. `processor.py` never imports the
kernel; it receives a callable and delegates to it.

### What was NOT changed

* CandidateSemanticIR — still exactly 18 fields (`LEGACY_FIELDS` 7 +
  `EXPANDED_FIELDS` 11, asserted by test).
* Accounting kernel semantics, formula authority, finance knowledge
  authority.
* Grounding semantics (`ExpandedGroundingGate` untouched).
* Model weights. No retraining.
* Frozen datasets and training provenance.
* The existing `ingestion/parser.py` output, and `POST /v1/process`.

---

## 3. Files changed

### Added — `backend/document_understanding/` (new package)

| File | Role |
|---|---|
| `__init__.py` | Package boundary; exports evidence types only |
| `representation.py` | `DocumentRepresentation`, `PageRepresentation`, `EvidenceRef`, status constants |
| `page_accounting.py` | Per-page read + `DIGITAL_TEXT`/`IMAGE_ONLY`/`EXTRACTION_FAILED` classification |
| `adapter.py` | `EvidenceAdapter` — deterministic evidence normalization |
| `ocr.py` | `OCRProvider` interface, `NullOCRProvider`, `FixtureOCRProvider` |
| `ocr_rapid.py` | RapidOCR / PP-OCRv5 provider (lazy import) |
| `ocr_tesseract.py` | Tesseract 5 provider (lazy import, fallback) |
| `registry.py` | Engine selection; `PLATRIXA_OCR_ENGINE` |
| `processor.py` | Document → evidence → existing pipeline, with lineage |
| `inputs.py` | Transport-level upload validation |

### Added — tests, benchmarks, docs, manifest

`scripts/fte_document_understanding_phase1.py` (63 checks) ·
`scripts/fte_document_understanding_phase2.py` (46) ·
`scripts/fte_document_understanding_phase3.py` (51) ·
`scripts/fte_document_benchmark.py` ·
`requirements-ocr.txt` ·
`docs/PLATRIXA_DOCUMENT_UNDERSTANDING_AUDIT.md` (pre-flight audit) ·
this report

### Modified (two files, both additive)

* `api/schemas.py` — added `DeveloperDocumentProcessResponse` only.
* `api/routes/developer.py` — added `POST /v1/process/document` and a
  bounded multipart reader. Existing routes untouched.
* `docs/HOSTED_API.md` — document endpoint documentation.

---

## 4. The Phase 1 bug that started this

The pre-flight audit found that `ingestion.parser.parse_pdf` emits a
`========== PAGE N ==========` marker **only when `page.extract_text()`
returns text**. A scanned page therefore left no trace, and
`layout_extractor.pages_without_text` could never fire for a real scan.

The fix does **not** modify the existing parser (that would risk every
existing caller and fixture). Instead the new page-accounting module does
its own per-page read and represents every page, so:

* the existing parser stays byte-compatible, and
* the existing `pages_without_text` signal now works, because the page
  marker is always emitted (Phase 1 test asserts it now returns `[1]` for a
  real scan — it previously returned `[]`).

---

## 5. Tests

| Suite | Checks | Result |
|---|---|---|
| Phase 1 — page accounting + evidence adapter | 63 | **63/63 PASS** |
| Phase 2 — OCR integration | 46 | **46/46 PASS** |
| Phase 3 — document → semantic → 18-field → grounding | 51 | **51/51 PASS** |
| Existing `fte_layout_extraction_test.py` | 28 | **28/28 PASS** (unchanged) |
| Existing `fte_fyjc_51_model_provider_test.py` | — | PASS (unchanged) |

Coverage required by the phase, all present: normal text PDF · image-only
PDF · mixed text/image PDF · empty page · extraction failure · multi-page ·
page-numbering correctness · evidence identity/stability · deterministic
repeat processing.

Phase 3 covers all eight required scenarios: image invoice · scanned
invoice PDF · digital invoice PDF · bank statement · mixed text/image PDF ·
ambiguous document · unsupported information · OCR corruption/noise.

### Verified safety properties (each is an assertion, not a claim)

* A hallucinating interpreter (invented party + invented amount) is
  rejected with `GROUNDING_FAILED`, naming the unsupported values.
* A smuggled accounting field in the candidate → `FORBIDDEN_OUTPUT`.
* A scanned document with **no OCR engine installed** → `GROUNDING_FAILED`,
  `success=false`. No text is invented to let it through.
* A corrupt PDF → one `EXTRACTION_FAILED` page, and OCR is **not** allowed
  to resurrect it (a bug found and fixed during Phase 2 testing).
* No module in `backend/document_understanding/` imports `backend.maths`
  or `backend.kernel` (checked by AST, not grep).
* `POST /v1/process` behaves exactly as before.

---

## 6. OCR engine and licensing

| Item | Engine | License | Status here |
|---|---|---|---|
| Preferred | RapidOCR / PP-OCRv5 (ONNX) | **Apache-2.0** | integrated, **not installed in this sandbox** |
| Fallback | Tesseract 5 | **Apache-2.0** | integrated, **not installed in this sandbox** |
| Rasterizer | PyMuPDF | **AGPL-3.0 or commercial** | see note |
| Chosen **against** | MinerU (AGPL-3.0), Marker / Surya (GPL-3.0, weights CC-BY-NC-SA) | — | rejected on license for a hosted service despite stronger published benchmarks |

**Version:** the engine reports its own version at runtime
(`RapidOCRProvider.version`); it is recorded in every evidence record and
in the API response. In this environment the selected engine is `none`.

**PyMuPDF note (important):** it is used only to rasterize pages before
recognition. If AGPL-3.0 is unacceptable for your distribution, omit it and
rasterize differently; every scanned page then fails closed as
`IMAGE_ONLY` rather than being misread. ONNX Runtime is MIT.

**Dependency isolation:** `requirements-ocr.txt` is a separate, optional
manifest. `requirements-core.txt` and `requirements.txt` are **unchanged**.
Both engine modules import lazily, so a text-only installation never loads
them and the document path runs unchanged with no OCR at all.

---

## 7. Benchmark methodology

* **Fixtures:** synthetic only, generated with reportlab/PIL. No private or
  third-party financial document is used; nothing is sent to any external
  service.
* **Classes:** DIGITAL PDF (1/5/10 p) · SCANNED PDF (1/5 p, image-only,
  no text layer) · IMAGE (standalone PNG) · MIXED PDF (text page + scan).
* **Repeats:** 5 per case; cold = first call, warm = median of the rest.
* **Stages timed separately:** page accounting/text extraction · OCR ·
  evidence adapter · semantic+schema+grounding · total end-to-end.
* **Interpreter:** the real `Kernel` with a stub model provider, because
  the real 1.5B model cannot be downloaded here (~3 GB required, 1.5 GB
  free). **Model inference latency is therefore NOT MEASURED** and is not
  reported as a number.
* **Environment:** 2 vCPU, ~3 GB RAM, **no GPU**.

Reproduce with:

```bash
PYTHONPATH=. python3 scripts/fte_document_benchmark.py --json bench.json
```

---

## 8. Benchmark results (MEASURED)

All values milliseconds, warm median, unless noted. `N/M` = NOT MEASURED.

| Case | Pg | KB | extract | OCR | adapter | semantic+schema+ground | **e2e** | ms/pg | pages/s |
|---|---|---|---|---|---|---|---|---|---|
| DIGITAL PDF 1p | 1 | 1.4 | 0.92 | N/M | 0.83 | 98.27 | **63.46** | 63.46 | 15.8 |
| DIGITAL PDF 5p | 5 | 3.6 | 3.09 | N/M | 3.90 | 82.71 | **82.95** | 16.59 | 60.3 |
| DIGITAL PDF 10p | 10 | 6.2 | 5.85 | N/M | 5.94 | 100.08 | **104.40** | 10.44 | 95.8 |
| SCANNED PDF 1p | 1 | 47.3 | 0.75 | N/M | 0.73 | 0.26 | **0.85** | 0.85 | 1177 |
| SCANNED PDF 5p | 5 | 232.7 | 2.65 | N/M | 2.62 | 0.11 | **2.77** | 0.55 | 1809 |
| IMAGE 1p | 1 | 7.9 | 0.04 | N/M | 0.04 | 0.06 | **0.09** | 0.09 | 10949 |
| MIXED PDF 2p | 2 | 45.7 | 1.26 | N/M | 1.23 | 60.79 | **61.89** | 30.94 | 32.3 |

Cold starts were within ~1.6× of warm (e.g. DIGITAL 1p: 99.15 ms cold).
Peak RSS across the whole run: **334.9 MB**. Peak VRAM: **N/A — no GPU**.

### Reading these numbers honestly

* **The SCANNED/IMAGE rows are fast because OCR did not run.** Without an
  engine, those documents fail closed almost immediately. They are *not*
  evidence that scanned-document processing is sub-millisecond in
  production. **OCR latency is NOT MEASURED** in this environment.
* The semantic column uses a stub interpreter. With the real 1.5B model,
  that column becomes model-bound (this repository's own live-provider runs
  recorded ~2.8–15 s per call).
* Combined with published figures (Tesseract ≈0.8–2 s/page CPU, PP-OCRv5
  ≈0.3–1 s/page CPU), a realistic scanned-document end-to-end is expected
  in the **seconds per page** range on this CPU-only class of machine.

### Quality (synthetic ground truth only)

| Case | pages accounted | text extracted | status | success |
|---|---|---|---|---|
| DIGITAL 1p/5p/10p | 1/1 · 5/5 · 10/10 ✅ | correct | `VERIFIED` | true |
| SCANNED 1p / 5p | 1/1 · 5/5 ✅ | none (correct: no OCR) | **`GROUNDING_FAILED`** | **false** |
| IMAGE 1p | 1/1 ✅ | none (correct: no OCR) | **`GROUNDING_FAILED`** | **false** |
| MIXED 2p | 2/2 ✅ | correct (digital page) | `VERIFIED` | true |

**False-VERIFIED rate: 0** across all cases. Notably, the scanned and image
cases — the ones a naive implementation would quietly "handle" by
inventing text — fail closed exactly as designed.

**OCR accuracy: NOT MEASURED.** No engine is installed, and no accuracy
figure is claimed anywhere in this report.

---

## 9. Evidence behavior

* `document_id` = `doc_<name>_<sha256[:16]>` — content-addressed, so the
  same file always gets the same ID and two different files never collide.
* `evidence_id` = `<document_id>:p<page>:e<ordinal>` — deterministic and
  stable across repeated processing (asserted by test).
* Every evidence record carries page, text, bbox (when the engine supplies
  one), confidence (or `None` — never a fabricated 1.0), source type, and
  OCR engine + version.
* Digital text is split to one block per line, because that is the finest
  granularity the existing grounding gate can actually verify.
* `DocumentProcessorResult.evidence_for_field("parties"|"amounts"|
  "references")` returns the supporting page/bbox/text/confidence, giving
  the lineage: document → page → bbox → source text → semantic field.

---

## 10. Failure behavior (all fail-closed, all measured)

| Condition | Behavior |
|---|---|
| OCR not installed | Image pages stay `IMAGE_ONLY`; document fails closed |
| OCR returns nothing / raises / returns whitespace | Page stays `IMAGE_ONLY`; note recorded |
| Region confidence < 0.30 | Excluded from evidence entirely (never trusted) |
| Corrupt / truncated PDF | One `EXTRACTION_FAILED` page; **OCR is not allowed to resurrect it** |
| Ambiguous document | Runtime refusal (`BLOCKED` / `NOT_SUPPORTED` / `REVIEW_REQUIRED`) |
| Hallucinated interpretation | `GROUNDING_FAILED`, naming the unsupported values |
| Smuggled accounting field | `FORBIDDEN_OUTPUT` |
| Over-limit upload / unsupported type | `413` / `415` transport errors, not verdicts |

---

## 11. Limitations and known gaps

1. **OCR accuracy is unproven.** No engine was installable here. Every
   accuracy claim remains open until benchmarked against a real corpus.
2. **The semantic-model column is not measured** (model too large for this
   sandbox). Only the deterministic stages are timed.
3. **A golden real-document corpus does not exist yet.** All fixtures are
   synthetic. This is the single biggest gap to closing before any
   accuracy claim.
4. **Weak fact-graph consumption (pre-existing).** The audit measured
   `FinancialExtractorV2` extracting ~0 facts from good tables. OCR does not
   fix that, and noisier text may make it worse. It is untouched here
   because it is outside the document-layer scope.
5. **Digit errors remain the core risk.** Grounding proves a number came
   from the text; it cannot prove the text matches the pixels. A misread
   digit is well-formed, grounded, and wrong. Mitigation today is the
   confidence floor plus fail-closed review states.
6. **Truncation at 2 000 characters** (matching the existing developer-API
   input limit) means a long document is interpreted only up to the cap; the
   truncation is recorded in `notes`, never silent.
7. **`bbox` is absent for digitally-extracted text** — pypdf does not expose
   per-line geometry here. It is `null`, not invented.
8. **No page-level parallelism or caching yet.** Correctness first, as
   instructed; the current end-to-end times do not yet require it.

---

## 12. Future work

1. Install `requirements-ocr.txt` and re-run the benchmark to replace every
   `NOT MEASURED` cell with a measurement, on CPU and on a GPU host.
2. Build the golden real-document corpus (20–30 real invoices, bills,
   statements, report pages; digital and scanned) and measure true field
   accuracy, page/evidence preservation, and false-VERIFIED rate.
3. Per-page interpretation instead of one call per document, so a 10-page
   statement does not become a single long-context request.
4. Wire the adapter's confidence into the existing 15H
   Good/Uncertain/Unusable taxonomy so `EXTRACTION_FAILURE` reasons surface
   in existing review mechanisms.
5. Improve the fact-graph consumer before relying on OCR-fed tables.
6. Add deterministic, content-addressed caching of document evidence.
7. Resolve the PyMuPDF AGPL question for the intended distribution.

---

## 13. Acceptance criteria

| Criterion | Status |
|---|---|
| Existing text pipeline passes regression | ✅ layout 28/28, provider suite ✅, `/v1/process` unchanged |
| Every PDF page accounted for | ✅ incl. image-only and failed pages |
| Image-only pages explicitly detectable | ✅ `IMAGE_ONLY` + `pages_needing_ocr` |
| OCR behind a replaceable provider interface | ✅ `OCRProvider`, registry, two engines |
| Evidence contains source/page information | ✅ id, page, bbox, text, confidence, engine |
| OCR cannot produce financial truth | ✅ no maths/kernel imports; AST-asserted |
| Documents reach the existing 18-field contract | ✅ no new schema; 18 asserted |
| Schema verification still enforced | ✅ `VALIDATION_FAILED` / `FORBIDDEN_OUTPUT` cases |
| Grounding still enforced | ✅ hallucination → `GROUNDING_FAILED` |
| Authorities unchanged | ✅ untouched |
| No new CandidateSemanticIR fields | ✅ 7 + 11 = 18, asserted |
| No kernel bypass | ✅ processor injects and delegates; never imports kernel |
| Ambiguous/unsupported fail closed | ✅ measured across 8 scenarios |
| Developer API accepts document inputs | ✅ `/v1/process/document` (text/PDF/image) |
| Benchmark reproducible | ✅ `scripts/fte_document_benchmark.py` |
| Latency/throughput measured where possible | ✅ measured; gaps labelled NOT MEASURED |
| Documentation explains the architecture | ✅ this report + `docs/HOSTED_API.md` |
| No frozen artifacts or weights modified | ✅ verified |
