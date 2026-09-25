"""Platrixa real-invoice E2E benchmark harness.

Run ONCE from the repository root:

    python3 scripts/platrixa_real_invoice_e2e.py [--output-dir DIR] [--limit N]

Routes every corpus case through the LIVE production pipeline only:

    text_for_model (corpus input)
      → Kernel.process(raw_input)                     backend/kernel/kernel.py
          → ModelProvider.interpret()                 (deterministic benchmark stub;
                                                       no model weights touched)
          → CandidateSemanticIR                       backend/semantics/ir.py
          → StructuredInterpretationValidator         backend/maths/schema_verifier.py
          → ExpandedGroundingGate                     backend/maths/fyjc_grounding_gate.py
          → GroundedSemanticIR                        backend/semantics/ir.py
          → deterministic accounting (existing flow)  via Kernel.process_accounting
          → KernelResult.status                       VERIFIED / REVIEW_REQUIRED /
                                                      BLOCKED / UNSUPPORTED_TRANSACTION ...

No production accounting, formula, knowledge, schema, grounding, API, or model
code is modified.  The model stage uses a deterministic stub (documented), so
model-inference latency is NOT measured here — it is measured separately in a
GPU environment (see docs/PLATRIXA_INVOICE_CAPACITY_REPORT.md).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.kernel.kernel import Kernel  # noqa: E402
from backend.model_provider.base import (  # noqa: E402
    InterpretationResult,
    ModelProvider,
    ProviderStatus,
)
from backend.semantics.ir import GROUNDABLE_FIELDS, SCHEMA_VERSION  # noqa: E402

DEFAULT_INPUT = Path("benchmark/real_invoice_e2e/corpus.json")
DEFAULT_OUTPUT = Path("/tmp/platrixa_real_e2e_out")

STUB_MODEL_ID = "benchmark-deterministic-stub-v2"
STUB_REVISION = "no-weights"

STATUS_FAMILIES = (
    "VERIFIED", "REVIEW_REQUIRED", "BLOCKED", "UNSUPPORTED_TRANSACTION",
    "VALIDATION_FAILED", "GROUNDING_FAILED", "FORBIDDEN_OUTPUT", "MODEL_UNAVAILABLE",
)


# --------------------------------------------------------------------------- #
# Deterministic benchmark provider: replays the corpus candidate (already
# verified against the live validator + grounding gate at corpus build time).
# It is a ModelProvider in the production contract sense; it touches no model
# weights and is reported as STUB in every result block.
# --------------------------------------------------------------------------- #
class CorpusReplayProvider:
    """ModelProvider implementation that deterministically replays the corpus
    candidate. No learning, no weights, no network."""

    def __init__(self) -> None:
        self.calls = 0

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            available=True,
            model_id=STUB_MODEL_ID,
            base_model_revision=STUB_REVISION,
            adapter_repo_id="",
            adapter_revision="",
            reason="deterministic corpus replay; model latency NOT measured",
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        self.calls += 1
        case = self._by_text.get(raw_input)
        if case is None:
            raise ValueError("input does not match any corpus case (stub is replay-only)")
        return InterpretationResult(
            raw_input=raw_input,
            candidate=case["candidate_18"],
            model_id=STUB_MODEL_ID,
            provider_revision=STUB_REVISION,
            generated_profile={"source": "corpus_replay"},
        )

    _by_text: dict


# --------------------------------------------------------------------------- #
def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _load_corpus(path: Path) -> dict:
    data = json.loads(path.read_text())
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Platrixa real-invoice E2E benchmark")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--limit", type=int, default=None,
                        help="run only the first N cases")
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.is_absolute():
        input_path = PROJECT_ROOT / input_path
    if not input_path.exists():
        print(f"ERROR: corpus not found at {input_path}\n"
              f"Generate it first:  python3 benchmark/real_invoice_e2e/spec_gen.py",
              file=sys.stderr)
        return 2

    corpus = _load_corpus(input_path)
    cases = corpus["cases"]
    if args.limit:
        cases = cases[: args.limit]

    provider = CorpusReplayProvider()
    provider._by_text = {c["text_for_model"]: c for c in cases}
    kernel = Kernel(model_provider=provider)  # live Kernel: validator → gate → accounting

    per_case: dict[str, dict] = {}
    e2e_ms: list[float] = []
    model_ms: list[float] = []
    t_start = time.perf_counter()

    for case in cases:
        cid = case["invoice_id"]
        text = case["text_for_model"]
        entry: dict = {
            "scenario": case["scenario"],
            "expected_status": case["expected_status"],
            "expected_transaction_type": case["expected_transaction_type"],
            "expected_authority": case["expected_authority"],
        }
        try:
            t0 = time.perf_counter()
            result = kernel.process(text, request_id=f"bench:{cid}")
            t1 = time.perf_counter()

            e2e = (t1 - t0) * 1000.0
            # Stub interpret is ~0; measure it anyway and label as STUB.
            t2 = time.perf_counter()
            provider.interpret(text)
            t3 = time.perf_counter()
            model_ms.append((t3 - t2) * 1000.0)

            acct = result.accounting_result or {}
            entry.update({
                "final_status": result.status,
                "status_label": result.status_label,
                "verification_status": result.verification_status,
                "grounding_issues": result.grounding_issues,
                "issues": result.issues,
                "accounting_status": acct.get("status"),
                "accounting_authority": acct.get("authority") or acct.get("authority_id"),
                "accounting_operation": acct.get("operation") or acct.get("operation_id"),
                "status_matches_expected": result.status == case["expected_status"],
                "tx_matches_expected": bool(
                    result.candidate_ir is not None
                    and result.candidate_ir.transaction_type() == case["expected_transaction_type"]
                ),
                "grounded_ir_reached_accounting": result.grounded_ir is not None
                and result.accounting_result is not None,
                "e2e_latency_ms": round(e2e, 3),
                "model_latency_ms_stub": None,  # filled below
                "schema_version": SCHEMA_VERSION,
                "groundable_fields_present": sorted(
                    f for f in GROUNDABLE_FIELDS if result.grounded_ir is not None
                    and result.grounded_ir.fields.get(f) not in (None, [], {})
                ),
            })
            entry["model_latency_ms_stub"] = round((t3 - t2) * 1000.0, 3)
            e2e_ms.append(e2e)
        except Exception as exc:  # defensive: record and continue
            entry.update({
                "final_status": "HARNESS_ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "status_matches_expected": False,
            })
        per_case[cid] = entry

    total_wall_s = time.perf_counter() - t_start

    # ---- aggregates -------------------------------------------------------
    e2e_sorted = sorted(e2e_ms)
    n = len(e2e_sorted)
    status_counts = {s: 0 for s in STATUS_FAMILIES}
    matches = 0
    for entry in per_case.values():
        status_counts[entry.get("final_status", "HARNESS_ERROR")] = \
            status_counts.get(entry.get("final_status", "HARNESS_ERROR"), 0) + 1
        matches += 1 if entry.get("status_matches_expected") else 0

    throughput_per_sec = (n / total_wall_s) if total_wall_s > 0 else 0.0
    per_day = throughput_per_sec * 86400.0

    report = {
        "harness": {
            "name": "platrixa_real_invoice_e2e",
            "version": "2.0.0",
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "kernel_pipeline": "Kernel.process (live): provider → CandidateSemanticIR → "
                               "schema validator → ExpandedGroundingGate → GroundedSemanticIR "
                               "→ deterministic accounting → KernelResult",
            "model_provider": STUB_MODEL_ID,
            "measurement_labels": {
                "e2e_latency_ms": "MEASURED (live pipeline, stub model)",
                "model_latency_ms_stub": "MEASURED but STUB — NOT real model inference latency",
                "document_extraction_latency": "NOT MEASURED (spec-only corpus, no PDF/OCR stage)",
                "ocr_latency": "NOT MEASURED (no OCR engine in this environment)",
            },
        },
        "corpus": {
            "input": str(input_path),
            "cases_run": n,
            "generation_seed": corpus["manifest"]["generation_seed"],
            "scenario_counts": corpus["manifest"]["scenario_counts"],
        },
        "results": {
            "final_status_counts": status_counts,
            "status_matches_expected": matches,
            "status_agreement_rate": round(matches / n, 4) if n else 0.0,
            "end_to_end": {
                "p50_ms": round(_percentile(e2e_sorted, 0.50), 3),
                "p95_ms": round(_percentile(e2e_sorted, 0.95), 3),
                "p99_ms": round(_percentile(e2e_sorted, 0.99), 3),
                "total_wall_seconds": round(total_wall_s, 3),
                "throughput_cases_per_sec_MEASURED_stub_model": round(throughput_per_sec, 2),
                "projected_cases_per_day_DERIVED_from_stub": round(per_day),
                "note": "DERIVED projection uses the STUB model; real-model throughput "
                        "must come from the GPU benchmark and is NOT claimed here.",
            },
            "model_stage": {
                "latency_p50_ms_stub": round(_percentile(sorted(model_ms), 0.50), 3),
                "label": "STUB",
            },
            "ocr_stage": {
                "status": "NOT MEASURED",
                "reason": "no OCR engine installed in this environment; corpus is spec-only",
            },
        },
        "per_case": per_case,
    }

    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "results.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n")

    summary = {
        "cases_run": n,
        "final_status_counts": status_counts,
        "status_agreement_rate": report["results"]["status_agreement_rate"],
        "e2e_p50_p95_p99_ms": [
            report["results"]["end_to_end"]["p50_ms"],
            report["results"]["end_to_end"]["p95_ms"],
            report["results"]["end_to_end"]["p99_ms"],
        ],
        "throughput_cases_per_sec_MEASURED_stub_model":
            report["results"]["end_to_end"]["throughput_cases_per_sec_MEASURED_stub_model"],
        "results_file": str(out_path),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
