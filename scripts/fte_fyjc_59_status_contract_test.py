#!/usr/bin/env python3
"""
PLATRIXA — PHASE 9: STATUS-CONTRACT ADVERSARIAL TEST (fte_fyjc_59)
====================================================================

Phase 8 evidence: the remote specialist emitted suggested_status=VERIFIED
(T2/T4, conf 0.85/0.88) and the grounding gate correctly failed closed
(Rule 0). Root cause: training data teaches "clear input → VERIFIED"
(649/800 train targets) while the documented production contract says the
model must NEVER claim VERIFIED (fyjc_llm_specialist.py:126); the local path
enforces that contract (fyjc_local_model_runner.py:573) but the Phase 7R/7S
remote provider seam never received the same documented layer.

Remediation (Phase 9): the status-authority contract layer now lives at the
remote provider boundary (backend/model_provider/remote_hf.py), inherited by
BOTH transports, BEFORE schema validation / grounding / kernel see the
candidate.

This suite proves:

  A. Provider seam normalizes VERIFIED → REVIEW_REQUIRED (both transports)
  B. Non-VERIFIED values pass through byte-identically
  C. All other 17 fields remain untouched (no post-processing hack beyond
     the single documented field)
  D. The kernel receives a REVIEW_REQUIRED candidate and reaches deterministic
     accounting — no GROUNDING_FAILED
  E. The grounding gate Rule 0 backstop remains fail-closed (NOT weakened)
  F. Local-path clamps and the documented contract text are unchanged
  G. Provider selection semantics unchanged

Run:  python3 scripts/fte_fyjc_59_status_contract_test.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import requests  # noqa: E402

from backend.model_provider.base import (  # noqa: E402
    ADAPTER_REPO_ID,
    ADAPTER_REVISION,
    BASE_MODEL_ID,
    BASE_MODEL_REVISION,
)
import backend.model_provider.remote_hf as remote_hf_module  # noqa: E402

# ---------------------------------------------------------------------------
# Shared check machinery (house style, mirrors fte_fyjc_57)
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_MESSAGES: list = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        _FAIL += 1
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))
    return ok


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_CANDIDATE: Dict[str, Any] = {
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

MODEL_INFO = {
    "base_model_id": BASE_MODEL_ID,
    "base_revision": BASE_MODEL_REVISION,
    "adapter_repo_id": ADAPTER_REPO_ID,
    "adapter_revision": ADAPTER_REVISION,
    "model_loaded": True,
    "adapter_loaded": True,
    "error": "",
}

TEST_URL = "https://mock-modal.example.com"
VALID_INPUT = "purchased furniture from raj for rs.25000"


class _FakeResponse:
    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self._body = body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not json")


class _PostMock:
    """Context manager replacing requests.post with a canned responder."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self._original = None

    def __enter__(self):
        self._original = requests.post
        requests.post = lambda url, **kw: self._responder(url, kw)
        return self

    def __exit__(self, *exc) -> None:
        requests.post = self._original


def _ok_body(candidate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "interpretation": candidate if candidate is not None else dict(VALID_CANDIDATE),
        "model": dict(MODEL_INFO),
    }


def _make_provider():
    return remote_hf_module.RemoteHFModelProvider(url=TEST_URL, timeout=5.0)


# ---------------------------------------------------------------------------
# A. Provider seam: VERIFIED → REVIEW_REQUIRED
# ---------------------------------------------------------------------------

def test_provider_seam() -> None:
    print("\n--- A. Status-authority contract layer at the remote provider seam ---")

    # A1: model claims VERIFIED at high confidence (the exact Phase 8 T2/T4 shape)
    verified_candidate = dict(VALID_CANDIDATE)
    verified_candidate["suggested_status"] = "VERIFIED"
    with _PostMock(lambda url, kw: _FakeResponse(200, _ok_body(verified_candidate))):
        result = _make_provider().interpret(VALID_INPUT)
    check("A1 VERIFIED claim normalized to REVIEW_REQUIRED",
          result.candidate.get("suggested_status") == "REVIEW_REQUIRED",
          str(result.candidate.get("suggested_status")))

    # A2: case/whitespace variants are caught
    for variant in ("Verified", "VERIFIED ", " verified "):
        c = dict(VALID_CANDIDATE)
        c["suggested_status"] = variant
        with _PostMock(lambda url, kw, cand=c: _FakeResponse(200, _ok_body(cand))):
            r = _make_provider().interpret(VALID_INPUT)
        check(f"A2 variant {variant!r} normalized", r.candidate.get("suggested_status") == "REVIEW_REQUIRED")

    # A3: already-compliant values pass through untouched
    for compliant in ("REVIEW_REQUIRED",):
        c = dict(VALID_CANDIDATE)
        c["suggested_status"] = compliant
        with _PostMock(lambda url, kw, cand=c: _FakeResponse(200, _ok_body(cand))):
            r = _make_provider().interpret(VALID_INPUT)
        check(f"A3 {compliant!r} passes through unchanged",
              r.candidate.get("suggested_status") == compliant)

    # A4: ALL other 17 fields byte-identical (single-field contract layer,
    # no hidden post-processing beyond the documented field)
    c = dict(VALID_CANDIDATE)
    c["suggested_status"] = "VERIFIED"
    with _PostMock(lambda url, kw: _FakeResponse(200, _ok_body(c))):
        r = _make_provider().interpret(VALID_INPUT)
    others_out = {k: v for k, v in r.candidate.items() if k != "suggested_status"}
    others_in = {k: v for k, v in c.items() if k != "suggested_status"}
    check("A4 all 17 non-status fields byte-identical", others_out == others_in)

    # A5: Gradio transport (HF Space) inherits the same seam
    gradio_ok_body = {
        "interpretation": dict(verified_candidate),
        "model": {
            "base_model": BASE_MODEL_ID,
            "base_revision": BASE_MODEL_REVISION,
            "adapter_model": ADAPTER_REPO_ID,
            "adapter_revision": ADAPTER_REVISION,
            "model_loaded": True,
            "adapter_loaded": True,
        },
    }

    class _FakeGradioClient:
        def __init__(self, *a, **k) -> None:
            pass

        def predict(self, text, api_name=None):  # noqa: ANN001, ANN002
            return (gradio_ok_body, "{}")

    import gradio_client

    from backend.model_provider.hf_gradio import HFGradioModelProvider

    original_client = gradio_client.Client
    try:
        gradio_client.Client = _FakeGradioClient
        gp = HFGradioModelProvider(
            url="https://mock-space.example.com", timeout=5.0, token=""
        )
        gr = gp.interpret(VALID_INPUT)
    finally:
        gradio_client.Client = original_client
    check("A5 gradio transport inherits the status-authority layer",
          gr.candidate.get("suggested_status") == "REVIEW_REQUIRED",
          str(gr.candidate.get("suggested_status")))


# ---------------------------------------------------------------------------
# B. Kernel integration: no GROUNDING_FAILED when model claims VERIFIED
# ---------------------------------------------------------------------------

def test_kernel_integration() -> None:
    print("\n--- B. Kernel flow with a VERIFIED-claiming remote model ---")

    from backend.kernel.kernel import Kernel

    verified_candidate = dict(VALID_CANDIDATE)
    verified_candidate["suggested_status"] = "VERIFIED"
    with _PostMock(lambda url, kw: _FakeResponse(200, _ok_body(verified_candidate))):
        kernel = Kernel(model_provider=_make_provider())
        result = kernel.process(VALID_INPUT)

    check("B1 no GROUNDING_FAILED from a VERIFIED-claiming model",
          result.status != "GROUNDING_FAILED", result.status)
    check("B2 model-unavailable absent", result.status != "MODEL_UNAVAILABLE", result.status)
    check("B3 deterministic terminal state reached",
          result.status in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"), result.status)
    check("B4 no status-authority issue in kernel issues",
          not any("claim VERIFIED" in str(i) for i in (result.issues or [])),
          str(result.issues)[:120])


# ---------------------------------------------------------------------------
# C. Grounding gate backstop: MUST remain fail-closed (not weakened)
# ---------------------------------------------------------------------------

def test_gate_backstop_intact() -> None:
    print("\n--- C. Grounding gate Rule 0 backstop unchanged ---")

    from backend.maths.fyjc_grounding_gate import ExpandedGroundingGate

    gate = ExpandedGroundingGate()
    verified_candidate = dict(VALID_CANDIDATE)
    verified_candidate["suggested_status"] = "VERIFIED"
    grounding = gate.ground(verified_candidate, VALID_INPUT)
    check("C1 gate still fails a raw VERIFIED claim",
          not grounding.safe_for_kernel, str(grounding.issues)[:120])
    check("C2 gate Rule 0 message intact",
          any("Only the accounting kernel may produce VERIFIED" in str(i) for i in grounding.issues))

    src = (_REPO_ROOT / "backend" / "maths" / "fyjc_grounding_gate.py").read_text(encoding="utf-8")
    check("C3 gate source Rule 0 block present (unweakened)",
          "Cannot claim VERIFIED" in src and "Only the accounting kernel may produce VERIFIED" in src)


# ---------------------------------------------------------------------------
# D. Local-path contract + documentation unchanged
# ---------------------------------------------------------------------------

def test_documented_contract_intact() -> None:
    print("\n--- D. Documented contract and local-path enforcement unchanged ---")

    specialist_src = (_REPO_ROOT / "backend" / "maths" / "fyjc_llm_specialist.py").read_text(encoding="utf-8")
    check("D1 documented contract text present",
          'suggested_status MUST ALWAYS be "REVIEW_REQUIRED". Never set VERIFIED.' in specialist_src)
    check("D2 local-path clamp still present (specialist :390)",
          'enriched["suggested_status"] = "REVIEW_REQUIRED"  # AI never claims VERIFIED' in specialist_src)

    runner_src = (_REPO_ROOT / "backend" / "maths" / "fyjc_local_model_runner.py").read_text(encoding="utf-8")
    check("D3 local-path clamp still present (local runner :573)",
          '"suggested_status": "REVIEW_REQUIRED"' in runner_src)

    provider_src = (_REPO_ROOT / "backend" / "model_provider" / "remote_hf.py").read_text(encoding="utf-8")
    check("D4 remote seam documents the Phase 9 contract", "Status-authority contract (Phase 9)" in provider_src)


# ---------------------------------------------------------------------------
# E. Selection semantics unchanged
# ---------------------------------------------------------------------------

def test_selection_unchanged(monkeypatch_setup=None) -> None:
    print("\n--- E. Provider selection semantics unchanged ---")

    import os

    old_url = os.environ.get("PLATRIXA_MODEL_ENDPOINT_URL")
    old_transport = os.environ.get("PLATRIXA_MODEL_TRANSPORT")
    try:
        from backend.model_provider.hf_gradio import HFGradioModelProvider

        os.environ["PLATRIXA_MODEL_ENDPOINT_URL"] = "https://mock-space.example.com"
        os.environ["PLATRIXA_MODEL_TRANSPORT"] = "gradio"
        p = remote_hf_module.get_model_provider()
        check("E1 gradio transport selected", isinstance(p, HFGradioModelProvider),
              type(p).__name__)

        os.environ["PLATRIXA_MODEL_TRANSPORT"] = "http"
        p2 = remote_hf_module.get_model_provider()
        check("E2 http transport selected", type(p2).__name__ == "RemoteHFModelProvider", type(p2).__name__)
    finally:
        if old_url is None:
            os.environ.pop("PLATRIXA_MODEL_ENDPOINT_URL", None)
        else:
            os.environ["PLATRIXA_MODEL_ENDPOINT_URL"] = old_url
        if old_transport is None:
            os.environ.pop("PLATRIXA_MODEL_TRANSPORT", None)
        else:
            os.environ["PLATRIXA_MODEL_TRANSPORT"] = old_transport


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("PLATRIXA — PHASE 9: STATUS-CONTRACT ADVERSARIAL TEST")
    print("=" * 78)

    test_provider_seam()
    test_kernel_integration()
    test_gate_backstop_intact()
    test_documented_contract_intact()
    test_selection_unchanged()

    print()
    print("=" * 78)
    if _FAIL == 0:
        print(f"RESULT: PASS — {_PASS}/{_PASS + _FAIL} checks passed")
    else:
        print(f"RESULT: FAIL — {_PASS} passed, {_FAIL} failed")
    print("=" * 78)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
