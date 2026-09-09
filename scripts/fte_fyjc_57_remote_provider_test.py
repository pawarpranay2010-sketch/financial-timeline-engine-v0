"""
Platrixa — Phase 7R RemoteHFModelProvider + Modal inference service test
========================================================================

Focused regression proof for the Phase 7R Modal inference seam:

  A. Remote provider success path (200 + valid body → InterpretationResult).
  B. Fail-closed remote failures:
       B1 timeout → ModelUnavailableError
       B2 unreachable → ModelUnavailableError
       B3 HTTP 500 → ModelUnavailableError
       B4 HTTP 503 (remote fail-closed health) → ModelUnavailableError
       B5 HTTP 422 → MalformedOutputError
       B6 invalid JSON body → MalformedOutputError
       B7 missing required field → MalformedOutputError
       B8 forbidden accounting field → ForbiddenAccountingFieldError
       B9 empty input → MalformedOutputError
  C. status() is non-blocking: makes NO network calls, reports
     loadable=True for a configured endpoint, available only after success.
  D. Selection factory (Part 6):
       D1 env set → RemoteHFModelProvider
       D2 env unset → LocalHFModelProvider (unchanged local behavior)
       D3 explicit provider wins
  E. Kernel integration unchanged:
       E1 cold remote provider (available=False, loadable=True) passes the
          Kernel pre-flight and reaches interpret() (Phase 7H seam intact).
       E2 successful remote interpretation flows through schema validation,
          grounding, deterministic accounting, to a KernelResult.
       E3 remote unavailability maps to Kernel MODEL_UNAVAILABLE (no fallback
          to any local/deterministic path).
  F. Modal service fidelity:
       F1 locked base/adapter identities match backend pins exactly
       F2 pinned Phase 6B/6C runtime versions present (torch/transformers/
          peft/accelerate)
       F3 build_prompt() is byte-exact against the formatted SFT data
       F4 REQUIRED_FIELDS_18 matches the 18-field contract exactly

No real model is downloaded or loaded. No network access is required
(all HTTP is mocked). Run:

    python3 scripts/fte_fyjc_57_remote_provider_test.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Runtime setup (repository convention: backend/ is a namespace package)
# ---------------------------------------------------------------------------

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_ROOT = _REPO_ROOT

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests  # noqa: E402

from backend.model_provider.base import (  # noqa: E402
    ADAPTER_REPO_ID,
    ADAPTER_REVISION,
    BASE_MODEL_ID,
    BASE_MODEL_REVISION,
    ForbiddenAccountingFieldError,
    InterpretationResult,
    MalformedOutputError,
    ModelUnavailableError,
    ProviderStatus,
)
from backend.model_provider.local_hf import LocalHFModelProvider  # noqa: E402
from backend.model_provider.remote_hf import (  # noqa: E402
    ENDPOINT_URL_ENV,
    REQUIRED_FIELDS_18,
    RemoteHFModelProvider,
    endpoint_url,
    get_model_provider,
)
from backend.kernel.kernel import MODEL_UNAVAILABLE, Kernel  # noqa: E402

# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_PASS = 0
_FAIL = 0
_FAILURES: list = []


def check(name: str, condition: Any, detail: str = "") -> bool:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"PASS: {name}")
        return True
    _FAIL += 1
    _FAILURES.append((name, detail))
    print(f"FAIL: {name} ({detail})")
    return False


# ---------------------------------------------------------------------------
# Fixtures (mirrors the Phase 7H seam-test schema-valid candidate)
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

VALID_INPUT = "purchased furniture from raj for rs.25000"

REMOTE_MODEL_INFO = {
    "base_model_id": BASE_MODEL_ID,
    "base_revision": BASE_MODEL_REVISION,
    "adapter_repo_id": ADAPTER_REPO_ID,
    "adapter_revision": ADAPTER_REVISION,
    "model_loaded": True,
    "adapter_loaded": True,
    "error": "",
}

TEST_URL = "https://mock-modal.example.com"


class _FakeResponse:
    """Minimal requests.Response stand-in."""

    def __init__(self, status_code: int, body: Any, text: str = "") -> None:
        self.status_code = status_code
        self._body = body
        self.text = text if text else (
            body if isinstance(body, str) else json.dumps(body)
        )

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not json")


class _PostMock:
    """Context manager that replaces requests.post with a canned responder."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self._original = None
        self.calls: list = []

    def _record(self, url, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"url": url, **kwargs})
        return self._responder(self.calls[-1])

    def __enter__(self):
        self._original = requests.post
        requests.post = self._record
        return self

    def __exit__(self, *exc) -> None:
        requests.post = self._original


def _ok_body(candidate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "interpretation": candidate if candidate is not None else dict(VALID_CANDIDATE),
        "model": dict(REMOTE_MODEL_INFO),
    }


def _make_provider(**kwargs) -> RemoteHFModelProvider:
    kwargs.setdefault("url", TEST_URL)
    kwargs.setdefault("timeout", 5.0)
    return RemoteHFModelProvider(**kwargs)


# ---------------------------------------------------------------------------
# A. Success path
# ---------------------------------------------------------------------------

def test_success_path() -> None:
    print("\n--- A. Remote provider success path ---")

    with _PostMock(lambda call: _FakeResponse(200, _ok_body())):
        provider = _make_provider()
        result = provider.interpret(VALID_INPUT)

    check("A1 returns InterpretationResult", isinstance(result, InterpretationResult))
    check("A2 candidate preserved verbatim", result.candidate == VALID_CANDIDATE)
    check("A3 raw_input echoed", result.raw_input == VALID_INPUT)
    check("A4 model_id from config", result.model_id == BASE_MODEL_ID)
    check("A5 provider_revision = adapter revision", result.provider_revision == ADAPTER_REVISION)
    check("A6 remote identity recorded", result.generated_profile.get("remote_adapter_revision") == ADAPTER_REVISION)
    check("A7 status available after success", provider.status().available is True)

    called = _last_call_url_check()
    check("A8 POSTs to <url>/interpret", called)


def _last_call_url_check() -> bool:
    # The last _PostMock closed; re-run a minimal call to capture the URL.
    with _PostMock(lambda call: _FakeResponse(200, _ok_body())) as mock:
        _make_provider().interpret(VALID_INPUT)
    return bool(mock.calls) and mock.calls[0]["url"].endswith("/interpret")


# ---------------------------------------------------------------------------
# B. Fail-closed failures
# ---------------------------------------------------------------------------

def test_failures() -> None:
    print("\n--- B. Fail-closed remote failures ---")

    def _raises(exc: Exception):
        def _r(call):  # noqa: ANN001
            raise exc
        return _r

    with _PostMock(_raises(requests.Timeout("t"))):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B1 timeout → ModelUnavailableError", False, "no exception")
        except ModelUnavailableError as e:
            check("B1 timeout → ModelUnavailableError", "timeout" in str(e).lower(), str(e))

    with _PostMock(_raises(requests.ConnectionError("conn"))):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B2 unreachable → ModelUnavailableError", False, "no exception")
        except ModelUnavailableError as e:
            check("B2 unreachable → ModelUnavailableError", True, str(e))

    with _PostMock(lambda call: _FakeResponse(500, {"detail": "boom"})):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B3 HTTP 500 → ModelUnavailableError", False, "no exception")
        except ModelUnavailableError as e:
            check("B3 HTTP 500 → ModelUnavailableError", "500" in str(e), str(e))

    with _PostMock(lambda call: _FakeResponse(503, {"error": "model_unavailable"})):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B4 HTTP 503 → ModelUnavailableError", False, "no exception")
        except ModelUnavailableError as e:
            check("B4 HTTP 503 → ModelUnavailableError", "503" in str(e), str(e))

    with _PostMock(lambda call: _FakeResponse(422, {"detail": "bad shape"})):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B5 HTTP 422 → MalformedOutputError", False, "no exception")
        except MalformedOutputError as e:
            check("B5 HTTP 422 → MalformedOutputError", "422" in str(e), str(e))

    with _PostMock(lambda call: _FakeResponse(200, "not-json-at-all")):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B6 invalid JSON → MalformedOutputError", False, "no exception")
        except MalformedOutputError as e:
            check("B6 invalid JSON → MalformedOutputError", "JSON" in str(e), str(e))

    missing = dict(VALID_CANDIDATE)
    del missing["overall_confidence"]
    with _PostMock(lambda call: _FakeResponse(200, _ok_body(missing))):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B7 missing required field → MalformedOutputError", False, "no exception")
        except MalformedOutputError as e:
            check("B7 missing required field → MalformedOutputError", "overall_confidence" in str(e), str(e))

    forbidden = dict(VALID_CANDIDATE)
    forbidden["debit_lines"] = [{"account": "x", "amount": "1"}]
    with _PostMock(lambda call: _FakeResponse(200, _ok_body(forbidden))):
        try:
            _make_provider().interpret(VALID_INPUT)
            check("B8 forbidden accounting field → ForbiddenAccountingFieldError", False, "no exception")
        except ForbiddenAccountingFieldError as e:
            check("B8 forbidden accounting field → ForbiddenAccountingFieldError", "debit_lines" in str(e), str(e))

    try:
        _make_provider().interpret("   ")
        check("B9 empty input → MalformedOutputError", False, "no exception")
    except MalformedOutputError:
        check("B9 empty input → MalformedOutputError", True)

    try:
        p = _make_provider(url="")
        p.interpret(VALID_INPUT)
        check("B10 unconfigured endpoint → ModelUnavailableError", False, "no exception")
    except ModelUnavailableError as e:
        check("B10 unconfigured endpoint → ModelUnavailableError", ENDPOINT_URL_ENV in str(e), str(e))


# ---------------------------------------------------------------------------
# C. status() is non-blocking
# ---------------------------------------------------------------------------

def test_status_nonblocking() -> None:
    print("\n--- C. status() is non-blocking ---")

    def _boom(call):  # noqa: ANN001
        raise AssertionError("status() must not make network calls")

    with _PostMock(_boom):
        provider = _make_provider()
        st = provider.status()
        check("C1 status() makes no network calls", True)
        check("C2 configured endpoint → loadable=True", st.loadable is True)
        check("C3 not available before any success", st.available is False)
        check("C4 pinned identity in status", st.model_id == BASE_MODEL_ID and st.adapter_revision == ADAPTER_REVISION)
        check("C5 status returns ProviderStatus", isinstance(st, ProviderStatus))


# ---------------------------------------------------------------------------
# D. Selection factory
# ---------------------------------------------------------------------------

def test_selection() -> None:
    print("\n--- D. Selection factory (single selection point) ---")

    old = os.environ.get(ENDPOINT_URL_ENV)
    try:
        os.environ[ENDPOINT_URL_ENV] = TEST_URL
        p = get_model_provider()
        check("D1 env set → RemoteHFModelProvider", isinstance(p, RemoteHFModelProvider))

        os.environ.pop(ENDPOINT_URL_ENV, None)
        p2 = get_model_provider()
        check("D2 env unset → LocalHFModelProvider", isinstance(p2, LocalHFModelProvider))
        check("D2b endpoint_url() empty when unset", endpoint_url() == "")
    finally:
        if old is None:
            os.environ.pop(ENDPOINT_URL_ENV, None)
        else:
            os.environ[ENDPOINT_URL_ENV] = old

    sentinel = LocalHFModelProvider()
    check("D3 explicit provider wins", get_model_provider(provider=sentinel) is sentinel)


# ---------------------------------------------------------------------------
# E. Kernel integration (unchanged Kernel, remote provider behind the seam)
# ---------------------------------------------------------------------------

def test_kernel_integration() -> None:
    print("\n--- E. Kernel integration unchanged ---")

    # E1/E2: cold remote provider passes the pre-flight and flows through the
    # full deterministic pipeline.
    with _PostMock(lambda call: _FakeResponse(200, _ok_body())):
        kernel = Kernel(model_provider=_make_provider())
        result = kernel.process(VALID_INPUT)

    check("E1 cold remote provider passes Kernel pre-flight", result.status != MODEL_UNAVAILABLE, result.status)
    check(
        "E2 full pipeline reached deterministic accounting",
        result.status in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"),
        f"status={result.status} issues={result.issues}",
    )
    check("E2b candidate carried on result", isinstance(result.interpretation_candidate, dict))
    check("E2c verification ran", result.verification_status in ("GROUNDED", None) or result.grounding_issues == [])

    # E3: remote unavailability → MODEL_UNAVAILABLE, never a local fallback.
    def _conn_error(call):  # noqa: ANN001
        raise requests.ConnectionError("down")

    with _PostMock(_conn_error):
        kernel2 = Kernel(model_provider=_make_provider())
        result2 = kernel2.process(VALID_INPUT)

    check("E3 remote down → Kernel MODEL_UNAVAILABLE", result2.status == MODEL_UNAVAILABLE, result2.status)
    check("E3b no fallback interpretation produced", result2.interpretation_candidate is None)


# ---------------------------------------------------------------------------
# F. Modal service fidelity (static module checks; no model load)
# ---------------------------------------------------------------------------

_MODAL_PATH = _REPO_ROOT / "training" / "modal_inference.py"


def _load_modal_module():
    spec = importlib.util.spec_from_file_location("platrixa_modal_inference", _MODAL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_modal_service() -> None:
    print("\n--- F. Modal service fidelity ---")

    if not _MODAL_PATH.exists():
        check("F0 modal_inference.py exists", False, str(_MODAL_PATH))
        return
    check("F0 modal_inference.py exists", True)

    try:
        mod = _load_modal_module()
    except Exception as exc:  # noqa: BLE001
        check("F0 modal_inference.py imports", False, repr(exc))
        return
    check("F0 modal_inference.py imports", True)

    check("F1 base model id pin", mod.BASE_MODEL_ID == BASE_MODEL_ID)
    check("F1b base revision pin", mod.BASE_MODEL_REVISION == BASE_MODEL_REVISION)
    check("F1c adapter repo pin", mod.ADAPTER_REPO_ID == ADAPTER_REPO_ID)
    check("F1d adapter revision pin", mod.ADAPTER_REVISION == ADAPTER_REVISION)

    pkgs = " ".join(mod.INFERENCE_PACKAGES)
    check(
        "F2 pinned Phase 6B/6C runtime versions",
        all(p in pkgs for p in (
            "torch==2.11.0",
            "transformers==5.16.1",
            "peft==0.20.0",
            "accelerate==1.14.0",
        )),
        pkgs,
    )

    # F3: byte-exact prompt vs the formatted SFT data (test split, first record).
    formatted = _REPO_ROOT / "training_data" / "fyjc_specialist_test_formatted.jsonl"
    with open(formatted, encoding="utf-8") as fh:
        record = json.loads(fh.readline())
    text = record["text"]
    sep = "### Response:\n"
    expected_prompt = text.split(sep)[0] + sep
    sft_input = text.split("### Input:\n")[1].split("\n\n### Response:")[0]
    actual_prompt = mod.build_prompt(sft_input)
    check("F3 build_prompt byte-exact vs SFT data", actual_prompt == expected_prompt)

    # F4: 18-field contract matches the authoritative field list.
    from backend.maths.fyjc_contract import ALL_VALID_FIELDS

    check("F4 REQUIRED_FIELDS_18 == ALL_VALID_FIELDS", set(REQUIRED_FIELDS_18) == set(ALL_VALID_FIELDS),
          f"missing={sorted(set(ALL_VALID_FIELDS) - set(REQUIRED_FIELDS_18))} extra={sorted(set(REQUIRED_FIELDS_18) - set(ALL_VALID_FIELDS))}")

    # F5: forbidden-field guard exists in the Modal module too.
    forbidden_probe = dict(VALID_CANDIDATE)
    forbidden_probe["journal"] = "x"
    check(
        "F5 modal forbidden-field detection",
        mod._FORBIDDEN_ACCOUNTING_FIELDS & set(forbidden_probe.keys()) == {"journal"},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("PLATRIXA — PHASE 7R REMOTE PROVIDER + MODAL INFERENCE TEST")
    print("=" * 72)

    test_success_path()
    test_failures()
    test_status_nonblocking()
    test_selection()
    test_kernel_integration()
    test_modal_service()

    print("\n" + "=" * 72)
    print(f"RESULT: {_PASS} PASS, {_FAIL} FAIL")
    if _FAILURES:
        print("\nFailures:")
        for name, detail in _FAILURES:
            print(f"  - {name}: {detail}")
    print("=" * 72)
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
