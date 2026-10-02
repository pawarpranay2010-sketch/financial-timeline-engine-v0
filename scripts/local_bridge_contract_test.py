#!/usr/bin/env python3
"""
Platrixa — local bridge response-contract tests (focused)
==========================================================

Covers the response contract in backend/model_provider/local_bridge.py:

  1. valid JSON output           → exact {"interpretation", "model"} envelope,
                                    all 18 fields preserved, raw model output
                                    kept verbatim in an audit field
  2. malformed JSON              → explicit MalformedOutputError (reject)
  3. missing `suggested_status`  → explicit MalformedOutputError naming the
                                    field — never silently completed/fabricated
  4. forbidden accounting-authority fields → ForbiddenAccountingFieldError
  5. model returns "VERIFIED"    → normalized to REVIEW_REQUIRED and recorded;
                                    the model never carries VERIFIED out of
                                    the provider boundary

Also covered: wrong-shape body rejection, envelope passthrough, and one
end-to-end round trip per case through a local fake HTTP bridge. The fake
bridge is a test double for the BRIDGE only — no model runs here and none of
this is model evidence.

No network beyond 127.0.0.1 ephemeral port. No secrets. No side effects.

Exit codes: 0 = all pass, 1 = any fail.
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.model_provider.base import (  # noqa: E402
    ForbiddenAccountingFieldError,
    MalformedOutputError,
)
from backend.model_provider.local_bridge import (  # noqa: E402
    LocalBridgeModelProvider,
    build_provider_envelope,
    enforce_interpretation_contract,
)
from backend.model_provider.remote_hf import REQUIRED_FIELDS_18  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture: a complete, contract-valid 18-field candidate
# ---------------------------------------------------------------------------

def make_candidate(**overrides: Any) -> Dict[str, Any]:
    candidate: Dict[str, Any] = {
        "transaction_type": "PURCHASE",
        "parties": [],
        "amounts": [],
        "payment_method": "CASH",
        "references": [],
        "ambiguities": ["MISSING_PARTY"],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "CASH",
        "ambiguity_flags": ["MISSING_PARTY"],
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "field_confidences": [],
        "overall_confidence": "0.40",
        "suggested_status": "REVIEW_REQUIRED",
        "safety_flags": ["NONE"],
        "scope_flags": ["SINGLE_TRANSACTION"],
    }
    assert set(REQUIRED_FIELDS_18) == set(candidate.keys()), "fixture drifted from contract"
    candidate.update(overrides)
    return candidate


FAILURES: List = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"{'PASS' if condition else 'FAIL'}: {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)
    return condition


def expect_raises(name: str, fn, exc_type, must_contain: str = "") -> Exception:
    try:
        fn()
    except exc_type as exc:
        ok = must_contain in str(exc)
        check(name, ok, f"raised {type(exc).__name__}: {exc}")
        return exc
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"wrong exception {type(exc).__name__}: {exc}")
        return exc
    check(name, False, "no exception raised (should have been rejected)")
    return RuntimeError("no exception")


# ---------------------------------------------------------------------------
# Fake bridge (test double for the bridge only — not model evidence)
# ---------------------------------------------------------------------------

MODE = {"mode": "valid"}


class FakeBridge(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        if not request.get("text"):
            self.send_response(422)
            self.end_headers()
            return
        if self.path != "/interpret":
            self.send_response(404)
            self.end_headers()
            return

        mode = MODE["mode"]
        if mode == "valid":
            inner = json.dumps(make_candidate())
        elif mode == "malformed":
            inner = "this is definitely not json {oops"
        elif mode == "missing_suggested_status":
            candidate = make_candidate()
            candidate.pop("suggested_status")
            inner = json.dumps(candidate)
        elif mode == "forbidden":
            inner = json.dumps(make_candidate(journal_entry={"debit": "Cash"}))
        elif mode == "verified":
            inner = json.dumps(make_candidate(suggested_status="VERIFIED"))
        elif mode == "wrong_shape":
            inner = json.dumps({"hello": "world"})
        elif mode == "no_keys":
            # Bridge body carries neither contract key at all.
            body = json.dumps({"hello": "world"})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            return
        else:  # pragma: no cover
            inner = json.dumps(make_candidate())

        body = json.dumps({"interpretation_text": inner, "model": {}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args: Any) -> None:  # silence
        return


def run_provider(mode: str):
    """Run provider.interpret() against the fake bridge in `mode`."""
    MODE["mode"] = mode
    provider = LocalBridgeModelProvider(url=f"http://127.0.0.1:{SERVER.server_port}")
    return provider, provider.interpret("Paid 1250 cash for office stationery.")


# ---------------------------------------------------------------------------
# A. Envelope construction (pure, no network)
# ---------------------------------------------------------------------------

print("--- A. envelope construction (pure) ---")

raw_text = json.dumps(make_candidate())
envelope = build_provider_envelope({"interpretation_text": raw_text, "model": {}})
check(
    "A1 exact envelope shape (keys == interpretation, model)",
    set(envelope.keys()) == {"interpretation", "model"},
    f"keys={sorted(envelope.keys())}",
)
check(
    "A2 all 18 contract fields survive parsing",
    all(f in envelope["interpretation"] for f in REQUIRED_FIELDS_18),
    f"{sum(f in envelope['interpretation'] for f in REQUIRED_FIELDS_18)}/18",
)
check(
    "A3 raw model output preserved verbatim in audit field",
    envelope["model"].get("raw_model_output") == raw_text,
    f"parse_mode={envelope['model'].get('parse_mode')}",
)

# Passthrough envelope (bridge someday returns the exact Platrixa envelope)
passthrough = build_provider_envelope(
    {"interpretation": make_candidate(), "model": {"provider": "local-llama.cpp"}}
)
check(
    "A4 passthrough envelope accepted, bridge model identity retained",
    passthrough["interpretation"]["transaction_type"] == "PURCHASE"
    and passthrough["model"].get("provider") == "local-llama.cpp"
    and passthrough["model"].get("source_envelope") == "interpretation",
)

expect_raises(
    "A5 wrong-shape body rejected explicitly",
    lambda: build_provider_envelope({"hello": "world"}),
    MalformedOutputError,
    "neither 'interpretation' nor 'interpretation_text'",
)
expect_raises(
    "A6 non-JSON interpretation_text rejected by envelope builder",
    lambda: build_provider_envelope({"interpretation_text": "{{{ definitely not json"}),
    MalformedOutputError,
    "not valid JSON",
)

# ---------------------------------------------------------------------------
# B. Contract enforcement (pure, no network)
# ---------------------------------------------------------------------------

print("--- B. contract enforcement (pure) ---")

expect_raises(
    "B1 missing suggested_status rejected, naming the field",
    lambda: enforce_interpretation_contract(
        {k: v for k, v in make_candidate().items() if k != "suggested_status"}
    ),
    MalformedOutputError,
    "suggested_status",
)
try:
    enforce_interpretation_contract(
        {k: v for k, v in make_candidate().items() if k != "suggested_status"}
    )
except MalformedOutputError as exc:
    check(
        "B2 rejection is explicit (contract audit lists exactly the gap)",
        getattr(exc, "contract", {}).get("missing_fields") == ["suggested_status"],
        f"missing_fields={getattr(exc, 'contract', {}).get('missing_fields')}",
    )

expect_raises(
    "B3 forbidden accounting-authority field rejected",
    lambda: enforce_interpretation_contract(make_candidate(journal_entry={})),
    ForbiddenAccountingFieldError,
    "journal_entry",
)

verified_candidate = make_candidate(suggested_status="VERIFIED")
contract_info = enforce_interpretation_contract(verified_candidate)
check(
    "B4 VERIFIED normalized to REVIEW_REQUIRED and recorded, not silent",
    verified_candidate["suggested_status"] == "REVIEW_REQUIRED"
    and contract_info.get("suggested_status_clamped_to_review_required") is True
    and contract_info.get("suggested_status_raw") == "VERIFIED",
    f"now={verified_candidate['suggested_status']}, clamped={contract_info.get('suggested_status_clamped_to_review_required')}",
)

# ---------------------------------------------------------------------------
# C. End-to-end through the real provider + fake bridge (test double only)
# ---------------------------------------------------------------------------

print("--- C. provider round trip via local fake bridge (not model evidence) ---")

SERVER = HTTPServer(("127.0.0.1", 0), FakeBridge)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()

provider, result = run_provider("valid")
check(
    "C1 valid JSON: provider returns 18-field candidate",
    all(f in result.candidate for f in REQUIRED_FIELDS_18),
)
check(
    "C2 valid JSON: raw model output preserved in generated_profile audit field",
    result.generated_profile.get("raw_model_output") == json.dumps(make_candidate()),
)
check(
    "C3 valid JSON: contract trace records pass",
    provider.last_trace.get("contract", {}).get("passed") is True,
    f"envelope={provider.last_trace.get('envelope')}, "
    f"parse_mode={provider.last_trace.get('parse_mode')}",
)

MODE["mode"] = "missing_suggested_status"
provider = LocalBridgeModelProvider(url=f"http://127.0.0.1:{SERVER.server_port}")
expect_raises(
    "C4 missing suggested_status rejected at the provider boundary",
    lambda: provider.interpret("Paid 1250 cash"),
    MalformedOutputError,
    "suggested_status",
)
check(
    "C4b rejection recorded in contract audit trace (no silent fill)",
    provider.last_trace.get("contract", {}).get("missing_fields")
    == ["suggested_status"],
    f"trace={provider.last_trace.get('contract')}",
)

MODE["mode"] = "malformed"
provider = LocalBridgeModelProvider(url=f"http://127.0.0.1:{SERVER.server_port}")
expect_raises(
    "C5 malformed model JSON rejected at the provider boundary",
    lambda: provider.interpret("Paid 1250 cash"),
    MalformedOutputError,
    "not valid JSON",
)
check(
    "C5b malformed raw output preserved verbatim for audit",
    provider.last_trace.get("raw_response_text")
    == "this is definitely not json {oops",
)

MODE["mode"] = "forbidden"
provider = LocalBridgeModelProvider(url=f"http://127.0.0.1:{SERVER.server_port}")
expect_raises(
    "C6 forbidden accounting-authority field rejected by the provider",
    lambda: provider.interpret("Paid 1250 cash"),
    ForbiddenAccountingFieldError,
    "journal_entry",
)

provider, result = run_provider("verified")
check(
    "C7 model-claimed VERIFIED never leaves the provider boundary",
    result.candidate.get("suggested_status") == "REVIEW_REQUIRED"
    and provider.last_trace.get("contract", {}).get(
        "suggested_status_clamped_to_review_required"
    )
    is True
    and result.candidate.get("suggested_status") != "VERIFIED",
    f"candidate status={result.candidate.get('suggested_status')}, "
    f"raw={provider.last_trace.get('contract', {}).get('suggested_status_raw')}",
)

MODE["mode"] = "wrong_shape"
provider = LocalBridgeModelProvider(url=f"http://127.0.0.1:{SERVER.server_port}")
expect_raises(
    "C8 valid-JSON-but-wrong-schema model output rejected naming every gap",
    lambda: provider.interpret("Paid 1250 cash"),
    MalformedOutputError,
    "missing required fields",
)
check(
    "C8b all 18 gaps recorded, suggested_status among them",
    set(provider.last_trace.get("contract", {}).get("missing_fields") or [])
    == set(REQUIRED_FIELDS_18),
    f"n_gaps={len(provider.last_trace.get('contract', {}).get('missing_fields') or [])}",
)

MODE["mode"] = "no_keys"
provider = LocalBridgeModelProvider(url=f"http://127.0.0.1:{SERVER.server_port}")
expect_raises(
    "C9 bridge body with neither contract key rejected at provider boundary",
    lambda: provider.interpret("Paid 1250 cash"),
    MalformedOutputError,
    "neither 'interpretation' nor 'interpretation_text'",
)
check(
    "C9b wrong-shape raw body preserved for audit",
    bool(provider.last_trace.get("raw_response_text")),
    f"raw={provider.last_trace.get('raw_response_text')}",
)

SERVER.shutdown()

# ---------------------------------------------------------------------------

print("=" * 60)
if FAILURES:
    print(f"RESULT: {len(FAILURES)} FAIL — {FAILURES}")
    sys.exit(1)
print("RESULT: ALL PASS")
sys.exit(0)
