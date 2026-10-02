#!/usr/bin/env python3
"""
Platrixa — local bridge acceptance harness (Gate 1 / Gate 2)
=============================================================

Runs ONE input through the complete local path and records the six-layer
acceptance record demanded by the milestone:

    1. inference reached           bridge HTTP + raw model response + latency
    2. provider contract satisfied strict envelope/JSON parse, all 18 fields,
                                   no forbidden accounting fields, model never
                                   declares VERIFIED
    3. schema validation           StructuredInterpretationValidator verdict,
                                   pass or fail with exact errors
    4. grounding                   ExpandedGroundingGate verdict + issues
    5. deterministic kernel        accounting result / its refusal
    6. terminal status             the runtime's actual terminal state

DESIGN RULES
------------
  - Production provider selection is untouched: the LocalBridgeModelProvider is
    injected explicitly via Kernel(model_provider=...) — the factory
    get_model_provider() is never consulted for this path.
  - Nothing is fabricated. Every layer reports PASS / FAIL / NOT_REACHED with
    the underlying evidence; the raw model response is stored verbatim.
  - --wiring-check runs the SAME pipeline against an in-process synthetic
    candidate (copied from the existing boundary-test fixture) to prove the
    harness wiring itself. Its report is labelled
    "wiring_check_synthetic — NOT model evidence".
  - Provenance is embedded per Gate 0 (reports/LOCAL_MODEL_PROVENANCE.md).

EXIT CODES
    0  report written AND layer 1 passed (inference reached)   [live mode]
    2  report written BUT layer 1 failed (inference not reached) [live mode]
    0  wiring check produced a complete six-layer record        [wiring mode]
    1  harness error (no report written)

USAGE
    python3 scripts/local_bridge_acceptance.py
    python3 scripts/local_bridge_acceptance.py \
        --bridge http://127.0.0.1:8081 \
        --input "Paid 1,250 cash for office stationery."
    python3 scripts/local_bridge_acceptance.py --wiring-check
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.kernel.kernel import Kernel  # noqa: E402
from backend.model_provider.base import (  # noqa: E402
    InterpretationResult,
    ProviderConfig,
    ProviderStatus,
    contains_forbidden_accounting_fields,
)
from backend.model_provider.remote_hf import REQUIRED_FIELDS_18  # noqa: E402

DEFAULT_INPUT = "Paid \u20b91,250 cash for office stationery."
DEFAULT_OUT = "reports/local_bridge_acceptance.json"
# The wiring fixture's canonical input — the fixture's parties/amount/type only
# ground against this text (same pairing as the existing boundary test).
WIRING_FIXTURE_INPUT = "purchased furniture from raj for rs.25000"

PASS, FAIL, NOT_REACHED, NA = "PASS", "FAIL", "NOT_REACHED", "NOT_APPLICABLE"

# Synthetic candidate for --wiring-check. Copied verbatim from the existing
# committed fixture scripts/fte_fyjc_52_kernel_boundary_test.py::VALID_CANDIDATE.
# This is NOT model output and must never be reported as such.
WIRING_FIXTURE_CANDIDATE = {
    "transaction_type": "PURCHASE",
    "parties": ["raj"],
    "amounts": [{"value": "25000", "currency": "INR", "source": "explicit"}],
    "payment_method": "UNKNOWN",
    "references": [],
    "ambiguities": ["payment method not stated"],
    "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
    "transaction_type_enum": "PURCHASE",
    "payment_method_enum": "UNKNOWN",
    "ambiguity_flags": ["MISSING_PAYMENT_MODE"],
    "referenced_transaction_index": None,
    "referenced_party": None,
    "referenced_amount": None,
    "field_confidences": [],
    "overall_confidence": "0.50",
    "suggested_status": "REVIEW_REQUIRED",
    "safety_flags": ["NONE"],
    "scope_flags": ["SINGLE_TRANSACTION"],
}


# ---------------------------------------------------------------------------
# Provenance (Gate 0)
# ---------------------------------------------------------------------------

def provenance_block() -> Dict[str, Any]:
    from backend.model_provider.base import (
        ADAPTER_REPO_ID,
        ADAPTER_REVISION,
        BASE_MODEL_ID,
        BASE_MODEL_REVISION,
    )

    return {
        "verdict": "PROVENANCE_UNKNOWN",
        "record": "reports/LOCAL_MODEL_PROVENANCE.md",
        "device_artifact": "platrixa-phase-h-Q4_K_M.gguf (Android, ~941 MB reported)",
        "sha256": "NOT_MEASURED (file lives on the device; run sha256sum there)",
        "pinned_repository_identity": {
            "base_model": f"{BASE_MODEL_ID}@{BASE_MODEL_REVISION}",
            "adapter": f"{ADAPTER_REPO_ID}@{ADAPTER_REVISION}",
            "note": (
                "the pinned adapter is documented (hf_space/README.md) as the "
                "Phase 6C artifact; it is NOT asserted to be the identity of "
                "the device GGUF"
            ),
        },
        "rule": (
            "the filename 'phase-h' is assigned unconditionally by "
            "scripts/build_local_gguf_artifact.py and is not evidence of the "
            "training phase; no result from this artifact is attributed to any "
            "training phase"
        ),
    }


# ---------------------------------------------------------------------------
# Contract evaluation (layer 2 record; enforcement lives in the provider)
# ---------------------------------------------------------------------------

def evaluate_contract(candidate: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(candidate, dict):
        return {"checked": True, "passed": False, "reject_reason": "no candidate"}
    missing = [f for f in REQUIRED_FIELDS_18 if f not in candidate]
    forbidden = contains_forbidden_accounting_fields(candidate)
    status_raw = str(candidate.get("suggested_status", "")).strip().upper()
    if missing:
        return {
            "checked": True, "passed": False,
            "reject_reason": "missing required fields: " + ", ".join(missing),
            "missing_fields": missing,
        }
    if forbidden:
        return {
            "checked": True, "passed": False,
            "reject_reason": "forbidden accounting fields: " + ", ".join(forbidden),
            "forbidden_fields": forbidden,
        }
    return {
        "checked": True, "passed": True, "reject_reason": "",
        "missing_fields": [], "forbidden_fields": [],
        "suggested_status_raw": status_raw,
        "suggested_status_is_verified_claim": status_raw == "VERIFIED",
    }


# ---------------------------------------------------------------------------
# Six-layer derivation
# ---------------------------------------------------------------------------

def derive_layers(
    trace: Optional[Dict[str, Any]],
    result: Any,
    contract_evaluation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """trace=None → synthetic wiring mode (no network layer exists)."""
    issues: List[str] = list(getattr(result, "issues", []) or [])
    status = getattr(result, "status", "")
    grounding_issues = list(getattr(result, "grounding_issues", []) or [])
    accounting = getattr(result, "accounting_result", None)

    def has_issue(prefix: str) -> bool:
        return any(str(i).startswith(prefix) for i in issues)

    # --- Layer 1: inference reached -----------------------------------
    if trace is None:
        l1 = {
            "result": NA,
            "detail": "wiring check — in-process synthetic stub, no network",
        }
        l1_ok: Optional[bool] = None
    elif trace.get("http_ok"):
        l1_ok = True
        l1 = {
            "result": PASS,
            "bridge_url": trace.get("bridge_url"),
            "http_status": trace.get("http_status"),
            "latency_ms": trace.get("latency_ms"),
            "raw_model_response": trace.get("raw_response_text"),
        }
    else:
        l1_ok = False
        l1 = {
            "result": FAIL,
            "bridge_url": trace.get("bridge_url"),
            "attempts": trace.get("attempts"),
            "detail": "no HTTP 200 from the bridge — inference not reached",
        }

    # --- Layer 2: provider contract ------------------------------------
    contract = trace.get("contract") if trace is not None else contract_evaluation
    contract = contract or {"checked": False, "passed": False, "reject_reason": ""}
    if trace is None:
        l2 = dict(contract)
        l2["result"] = PASS if contract.get("passed") else FAIL
        l2["source"] = "synthetic candidate evaluated by harness (not model output)"
        l2_ok = bool(contract.get("passed"))
    elif l1_ok is False:
        l2 = {"result": NOT_REACHED, "detail": "inference not reached"}
        l2_ok = False
    elif has_issue("malformed model output:") or has_issue("forbidden accounting output:"):
        l2 = {
            "result": FAIL,
            "reject_reason": "; ".join(issues),
        }
        l2_ok = False
    else:
        l2 = dict(contract)
        l2["result"] = PASS if contract.get("passed") else FAIL
        if not contract.get("passed") and not contract.get("reject_reason"):
            l2["reject_reason"] = (
                "envelope/parse rejection before contract check "
                "(see raw_model_response and attempts)"
            )
        l2_ok = bool(contract.get("passed"))

    # --- Layers 3-5 depend on how far Kernel.process() progressed -------
    l3: Dict[str, Any]
    l4: Dict[str, Any]
    l5: Dict[str, Any]

    if status == "MODEL_UNAVAILABLE":
        l3 = {"result": NOT_REACHED, "detail": "kernel failed before inference"}
        l4 = {"result": NOT_REACHED}
        l5 = {"result": NOT_REACHED}
    elif status == "FORBIDDEN_OUTPUT":
        if l2.get("result") == PASS:
            l2 = {
                "result": FAIL,
                "reject_reason": "candidate IR rejected: " + "; ".join(issues),
                "note": "accounting-truth content smuggled past structural checks",
            }
        l3 = {"result": NOT_REACHED, "detail": "output rejected before schema validation"}
        l4 = {"result": NOT_REACHED}
        l5 = {"result": NOT_REACHED}
    elif status == "VALIDATION_FAILED":
        if has_issue("malformed model output:") or has_issue("forbidden accounting output:"):
            if l2.get("result") == PASS:
                l2 = {"result": FAIL, "reject_reason": "; ".join(issues)}
            l3 = {"result": NOT_REACHED, "detail": "provider rejected output"}
            l4 = {"result": NOT_REACHED}
            l5 = {"result": NOT_REACHED}
        elif has_issue("schema validation:"):
            l3 = {
                "result": FAIL,
                "errors": [i for i in issues if i.startswith("schema validation:")],
            }
            l4 = {"result": NOT_REACHED, "detail": "schema validation failed"}
            l5 = {"result": NOT_REACHED}
        elif has_issue("Empty input"):
            l3 = {"result": NOT_REACHED, "detail": "empty input"}
            l4 = {"result": NOT_REACHED}
            l5 = {"result": NOT_REACHED}
        else:
            l3 = {"result": FAIL, "errors": issues}
            l4 = {"result": NOT_REACHED}
            l5 = {"result": NOT_REACHED}
    elif status == "GROUNDING_FAILED":
        l3 = {"result": PASS, "detail": "schema validation passed (kernel proceeded)"}
        l4 = {"result": FAIL, "grounding_issues": grounding_issues, "errors": issues}
        l5 = {"result": NOT_REACHED, "detail": "grounding gate refused"}
    elif status == "UNSUPPORTED_TRANSACTION":
        l3 = {"result": PASS, "detail": "schema validation passed (kernel proceeded)"}
        l4 = {
            "result": PASS,
            "verification_status": getattr(result, "verification_status", None),
            "grounding_issues": grounding_issues,
        }
        l5 = {"result": FAIL, "detail": "deterministic accounting flow refused input",
              "errors": issues}
    else:
        # Terminal accounting states: VERIFIED / REVIEW_REQUIRED / BLOCKED / ...
        l3 = {"result": PASS, "detail": "schema validation passed (kernel proceeded)"}
        l4 = {
            "result": PASS,
            "verification_status": getattr(result, "verification_status", None),
            "grounding_issues": grounding_issues,
        }
        if accounting is not None:
            l5 = {
                "result": PASS,
                "accounting_status": accounting.get("status"),
                "accounting_keys": sorted(accounting.keys()),
            }
        else:
            l5 = {"result": FAIL, "detail": "no accounting result recorded"}

    l6 = {
        "result": "RECORDED",
        "terminal_status": status,
        "status_label": getattr(result, "status_label", ""),
        "next_action": getattr(result, "next_action", None),
        "issues": issues,
    }

    return {
        "1_inference_reached": l1,
        "2_provider_contract": l2,
        "3_schema_validation": l3,
        "4_grounding": l4,
        "5_deterministic_kernel": l5,
        "6_terminal_status": l6,
    }


def layers_summary(layers: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    deepest = 0
    complete = True
    for idx, key in enumerate(
        [
            "1_inference_reached", "2_provider_contract", "3_schema_validation",
            "4_grounding", "5_deterministic_kernel", "6_terminal_status",
        ]
    ):
        result = layers[key].get("result")
        if result == NOT_REACHED:
            complete = False
            break
        deepest = idx + 1
    return {
        "deepest_layer_reached": deepest,
        "six_layer_record_complete": complete,
    }


# ---------------------------------------------------------------------------
# Synthetic wiring stub (NOT a model, NOT evidence)
# ---------------------------------------------------------------------------

class _WiringStubProvider:
    """In-process stub used ONLY by --wiring-check. Never touches network."""

    def __init__(self) -> None:
        self._config = ProviderConfig(
            model_id="wiring-check-stub",
            base_model_revision="SYNTHETIC",
            adapter_repo_id="SYNTHETIC",
            adapter_revision="SYNTHETIC",
        )
        self._last_candidate: Optional[Dict[str, Any]] = None

    @property
    def config(self) -> ProviderConfig:
        return self._config

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            available=True,
            model_id=self._config.model_id,
            base_model_revision=self._config.base_model_revision,
            adapter_repo_id=self._config.adapter_repo_id,
            adapter_revision=self._config.adapter_revision,
            reason="wiring check stub (synthetic, not a model)",
            loadable=True,
        )

    def interpret(self, raw_input: str) -> InterpretationResult:
        self._last_candidate = dict(WIRING_FIXTURE_CANDIDATE)
        return InterpretationResult(
            raw_input=raw_input,
            candidate=self._last_candidate,
            model_id=self._config.model_id,
            provider_revision=self._config.adapter_revision,
            generated_profile={"synthetic": True},
        )


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def build_report(
    *,
    mode: str,
    raw_input: str,
    result: Any,
    layers: Dict[str, Dict[str, Any]],
    trace: Optional[Dict[str, Any]],
    bridge_url: Optional[str],
) -> Dict[str, Any]:
    kernel_dict = result.to_dict() if hasattr(result, "to_dict") else {}
    return {
        "schema": "platrixa.local-bridge-acceptance/1",
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": mode,
        "evidence_label": (
            "wiring_check_synthetic — NOT model evidence"
            if mode == "wiring_check"
            else "live_bridge — real inference path"
        ),
        "input": raw_input,
        "provenance": provenance_block(),
        "bridge": {"url": bridge_url} if bridge_url else None,
        "layers": layers,
        "summary": layers_summary(layers),
        "provider_trace": trace,
        "kernel_result": kernel_dict,
    }


def print_human(report: Dict[str, Any]) -> None:
    print("=" * 72)
    print("PLATRIXA LOCAL BRIDGE ACCEPTANCE")
    print("=" * 72)
    print(f"mode       : {report['mode']} ({report['evidence_label']})")
    print(f"input      : {report['input']}")
    print(f"provenance : {report['provenance']['verdict']}")
    if report.get("bridge"):
        print(f"bridge     : {report['bridge']['url']}")
    print("-" * 72)
    for key, layer in report["layers"].items():
        print(f"[{layer.get('result', '?'):>14}] {key}")
        for field in (
            "detail", "reject_reason", "errors", "grounding_issues",
            "latency_ms", "http_status", "terminal_status", "status_label",
            "accounting_status", "verification_status", "issues", "attempts",
        ):
            if layer.get(field) not in (None, [], "", {}):
                print(f"               {field}: {layer[field]}")
    trace = report.get("provider_trace") or {}
    raw = trace.get("raw_model_response")
    if raw:
        print("-" * 72)
        print("raw model response (verbatim):")
        print(raw)
    print("-" * 72)
    summary = report["summary"]
    print(
        f"summary    : deepest layer = {summary['deepest_layer_reached']}/6, "
        f"six-layer record complete = {summary['six_layer_record_complete']}"
    )
    terminal = report["layers"]["6_terminal_status"].get("terminal_status")
    print(f"terminal   : {terminal}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run_wiring_check(raw_input: str, out_path: pathlib.Path) -> int:
    print("!! WIRING CHECK — synthetic stub, NOT model evidence\n")
    provider = _WiringStubProvider()
    kernel = Kernel(model_provider=provider)
    started = time.monotonic()
    result = kernel.process(raw_input)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)

    contract = evaluate_contract(result.interpretation_candidate)
    layers = derive_layers(trace=None, result=result, contract_evaluation=contract)
    report = build_report(
        mode="wiring_check",
        raw_input=raw_input,
        result=result,
        layers=layers,
        trace={"synthetic": True, "elapsed_ms": elapsed_ms, "contract": contract},
        bridge_url=None,
    )
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print_human(report)
    print(f"\nreport written: {out_path}")
    ok = report["summary"]["six_layer_record_complete"]
    print(f"RESULT: {'WIRING OK' if ok else 'WIRING BROKEN'}")
    return 0 if ok else 2


def run_live(
    raw_input: str, bridge_url: Optional[str], out_path: pathlib.Path
) -> int:
    from backend.model_provider.local_bridge import LocalBridgeModelProvider

    provider = LocalBridgeModelProvider(url=bridge_url) if bridge_url else LocalBridgeModelProvider()
    kernel = Kernel(model_provider=provider)
    started = time.monotonic()
    result = kernel.process(raw_input)  # exactly one inference
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)

    trace = dict(provider.last_trace)
    trace["kernel_total_elapsed_ms"] = elapsed_ms

    layers = derive_layers(trace=trace, result=result)
    report = build_report(
        mode="live_bridge",
        raw_input=raw_input,
        result=result,
        layers=layers,
        trace=trace,
        bridge_url=provider._url,
    )
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print_human(report)
    print(f"\nreport written: {out_path}")

    if layers["1_inference_reached"]["result"] == PASS:
        print("RESULT: INFERENCE REACHED — see layer results above")
        return 0
    print("RESULT: INFERENCE NOT REACHED — bridge unreachable or request rejected")
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Platrixa local bridge acceptance harness (six-layer record)."
    )
    parser.add_argument(
        "--input",
        default=None,
        help="raw input text (default: the Gate-2 acceptance sentence; "
        "for --wiring-check, the fixture's canonical input)",
    )
    parser.add_argument("--bridge", default="", help="bridge URL override")
    parser.add_argument(
        "--out",
        default=None,
        help="report output path (default: reports/local_bridge_acceptance.json; "
        "wiring check: reports/local_bridge_acceptance_wiring_check.json)",
    )
    parser.add_argument(
        "--wiring-check",
        action="store_true",
        help="run the harness against a synthetic in-process stub (no network)",
    )
    args = parser.parse_args()

    chosen_out = args.out or (
        "reports/local_bridge_acceptance_wiring_check.json"
        if args.wiring_check
        else DEFAULT_OUT
    )
    out_path = pathlib.Path(chosen_out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path

    try:
        if args.wiring_check:
            raw_input = args.input or WIRING_FIXTURE_INPUT
            return run_wiring_check(raw_input, out_path)
        raw_input = args.input or DEFAULT_INPUT
        return run_live(raw_input, args.bridge or None, out_path)
    except Exception as exc:  # noqa: BLE001 — harness must fail loudly, not silently
        print(f"HARNESS ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
