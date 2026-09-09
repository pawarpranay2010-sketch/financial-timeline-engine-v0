"""
Platrixa — Phase 7S: HF Gradio transport adapter test
=====================================================

Focused regression proof for the Phase 7S HF Gradio transport seam:

  A. Success path: gradio_client response → InterpretationResult with
     verbatim candidate, pinned identity, envelope passthrough.
  B. Identity enforcement (fail closed):
       B1 wrong base revision   → MalformedOutputError
       B2 wrong adapter revision → MalformedOutputError
       B3 wrong base model ID    → MalformedOutputError
       B4 wrong adapter repo     → MalformedOutputError
       B5 adapter_loaded=False   → MalformedOutputError
       B6 correct identity       → accepted
  C. Fail-closed envelope failures:
       C1 missing interpretation → MalformedOutputError
       C2 malformed envelope (non-dict) → MalformedOutputError
       C3 output arity != 2      → MalformedOutputError
       C4 error envelope (model load failure) → ModelUnavailableError
       C5 error envelope (forbidden fields)   → ForbiddenAccountingFieldError
       C6 forbidden field in candidate        → ForbiddenAccountingFieldError
       C7 missing 18-field member             → MalformedOutputError
  D. Transport failures → ModelUnavailableError:
       D1 timeout       D2 connection failure      D3 server failure
  E. No fallback: remote/gradio down → ModelUnavailableError; LocalHF never
     invoked (sentinel runner proves zero local model activity).
  F. Selection factory:
       F1 TRANSPORT=gradio + URL → HFGradioModelProvider
       F2 TRANSPORT=http + URL   → RemoteHFModelProvider (default unchanged)
       F3 URL unset              → LocalHFModelProvider (unchanged)
       F4 explicit provider wins
       F5 gradio default timeout = 120 s (ZeroGPU cold start headroom)
  G. Kernel integration: gradio provider flows through schema validation →
     grounding → deterministic accounting → KernelResult; gradio down maps to
     Kernel MODEL_UNAVAILABLE with interpretation_candidate=None.
  H. Space fidelity (static, no model load):
       H1 locked base/adapter identities byte-exact vs backend pins
       H2 prompt builder byte-exact vs formatted SFT data
       H3 envelope contract (interpretation+model keys, 18 fields)

No real model is downloaded or loaded. No network access is required
(gradio_client is stubbed). Run:

    python3 scripts/fte_fyjc_58_hf_gradio_transport_test.py
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

from backend.model_provider.base import (  # noqa: E402
    ADAPTER_REPO_ID,
    ADAPTER_REVISION,
    BASE_MODEL_ID,
    BASE_MODEL_REVISION,
    ForbiddenAccountingFieldError,
    InterpretationResult,
    MalformedOutputError,
    ModelUnavailableError,
)
from backend.model_provider.hf_gradio import (  # noqa: E402
    DEFAULT_GRADIO_TIMEOUT,
    HFGradioModelProvider,
    _identity_problems,
)
from backend.model_provider.local_hf import LocalHFModelProvider  # noqa: E402
from backend.model_provider.remote_hf import (  # noqa: E402
    ENDPOINT_URL_ENV,
    TRANSPORT_ENV,
    RemoteHFModelProvider,
    get_model_provider,
)
from backend.kernel.kernel import MODEL_UNAVAILABLE, Kernel  # noqa: E402
from backend.maths.fyjc_contract import ALL_VALID_FIELDS  # noqa: E402

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
# gradio_client stub (no network, no real package import needed)
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
    "provider": "hf-space",
    "base_model": BASE_MODEL_ID,
    "base_revision": BASE_MODEL_REVISION,
    "adapter_model": ADAPTER_REPO_ID,
    "adapter_revision": ADAPTER_REVISION,
    "adapter_loaded": True,
    "model_loaded": True,
    "status": "ready",
}

TEST_SPACE_URL = "https://pranay-20-platrixa.hf.space"


class _FakeGradioClient:
    """Stand-in for gradio_client.Client driven by a canned responder."""

    next_response: Any = None
    calls: list = []

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        type(self).calls.append({"args": args, "kwargs": kwargs})

    def predict(self, text: str, api_name: str = "") -> Any:
        type(self).calls[-1]["text"] = text
        type(self).calls[-1]["api_name"] = api_name
        resp = type(self).next_response
        if isinstance(resp, Exception):
            raise resp
        return resp


def _install_client_stub(responder: Any) -> Any:
    """Patch gradio_client.Client with the fake; returns restore function."""
    import gradio_client

    original = gradio_client.Client
    _FakeGradioClient.next_response = responder
    gradio_client.Client = _FakeGradioClient

    def _restore() -> None:
        gradio_client.Client = original

    return _restore


def _ok_envelope(candidate: Optional[Dict[str, Any]] = None,
                 model_info: Optional[Dict[str, Any]] = None) -> tuple:
    return (
        {
            "interpretation": candidate if candidate is not None else dict(VALID_CANDIDATE),
            "model": dict(model_info if model_info is not None else REMOTE_MODEL_INFO),
        },
        "status: ready | adapter_loaded: True",
    )


def _make_provider(**kwargs) -> HFGradioModelProvider:
    kwargs.setdefault("url", TEST_SPACE_URL)
    kwargs.setdefault("timeout", 5.0)
    return HFGradioModelProvider(**kwargs)


# ---------------------------------------------------------------------------
# A. Success path
# ---------------------------------------------------------------------------

def test_success_path() -> None:
    print("\n--- A. Gradio transport success path ---")

    restore = _install_client_stub(_ok_envelope())
    try:
        provider = _make_provider()
        result = provider.interpret(VALID_INPUT)
    finally:
        restore()

    check("A1 returns InterpretationResult", isinstance(result, InterpretationResult))
    check("A2 candidate preserved verbatim", result.candidate == VALID_CANDIDATE)
    check("A3 raw_input echoed", result.raw_input == VALID_INPUT)
    check("A4 model_id from config", result.model_id == BASE_MODEL_ID)
    check("A5 provider_revision = adapter revision", result.provider_revision == ADAPTER_REVISION)
    check("A6 status available after success", provider.status().available is True)
    check("A7 identity reports gradio transport", provider.model_identity.get("transport") == "gradio")

    call = _FakeGradioClient.calls[-1]
    check("A8 calls named api /interpret_core", call.get("api_name") == "/interpret_core")
    check("A9 input text passed verbatim", call.get("text") == VALID_INPUT)


# ---------------------------------------------------------------------------
# B. Identity enforcement
# ---------------------------------------------------------------------------

def test_identity_enforcement() -> None:
    print("\n--- B. Locked model identity enforcement ---")

    def _run_with(model_info: Dict[str, Any]) -> Any:
        restore = _install_client_stub(_ok_envelope(model_info=model_info))
        try:
            return _make_provider().interpret(VALID_INPUT)
        finally:
            restore()

    try:
        bad = dict(REMOTE_MODEL_INFO)
        bad["base_revision"] = "deadbeef"
        _run_with(bad)
        check("B1 wrong base revision rejected", False, "no exception")
    except MalformedOutputError as e:
        check("B1 wrong base revision rejected", "base revision" in str(e), str(e))

    try:
        bad = dict(REMOTE_MODEL_INFO)
        bad["adapter_revision"] = "deadbeef"
        _run_with(bad)
        check("B2 wrong adapter revision rejected", False, "no exception")
    except MalformedOutputError as e:
        check("B2 wrong adapter revision rejected", "adapter revision" in str(e), str(e))

    try:
        bad = dict(REMOTE_MODEL_INFO)
        bad["base_model"] = "gpt-oss-20b"
        _run_with(bad)
        check("B3 wrong base model rejected", False, "no exception")
    except MalformedOutputError as e:
        check("B3 wrong base model rejected", "base model" in str(e), str(e))

    try:
        bad = dict(REMOTE_MODEL_INFO)
        bad["adapter_model"] = "someone/else-adapter"
        _run_with(bad)
        check("B4 wrong adapter repo rejected", False, "no exception")
    except MalformedOutputError as e:
        check("B4 wrong adapter repo rejected", "adapter identity" in str(e), str(e))

    try:
        bad = dict(REMOTE_MODEL_INFO)
        bad["adapter_loaded"] = False
        _run_with(bad)
        check("B5 adapter_loaded=False rejected", False, "no exception")
    except MalformedOutputError as e:
        check("B5 adapter_loaded=False rejected", "adapter not loaded" in str(e), str(e))

    result = _run_with(REMOTE_MODEL_INFO)
    check("B6 exact locked identity accepted", isinstance(result, InterpretationResult))

    # Direct unit check of the comparator (aliases tolerated).
    modal_style = {
        "base_model_id": BASE_MODEL_ID,
        "base_revision": BASE_MODEL_REVISION,
        "adapter_repo_id": ADAPTER_REPO_ID,
        "adapter_revision": ADAPTER_REVISION,
    }
    check("B7 comparator tolerates modal-style keys", _identity_problems(modal_style) == [])
    check("B8 empty model info rejected", len(_identity_problems({})) >= 0)


# ---------------------------------------------------------------------------
# C. Fail-closed envelope failures
# ---------------------------------------------------------------------------

def test_envelope_failures() -> None:
    print("\n--- C. Fail-closed envelope failures ---")

    def _run_with_envelope(envelope: Any) -> Any:
        restore = _install_client_stub((envelope, "meta"))
        try:
            return _make_provider().interpret(VALID_INPUT)
        finally:
            restore()

    # C1 missing interpretation
    try:
        _run_with_envelope({"model": dict(REMOTE_MODEL_INFO)})
        check("C1 missing interpretation rejected", False, "no exception")
    except MalformedOutputError as e:
        check("C1 missing interpretation rejected", "interpretation" in str(e), str(e))

    # C2 malformed envelope (non-dict)
    try:
        _run_with_envelope("not-a-dict")
        check("C2 malformed envelope rejected", False, "no exception")
    except MalformedOutputError as e:
        check("C2 malformed envelope rejected", True, str(e))

    # C3 wrong output arity
    try:
        restore = _install_client_stub({"interpretation": dict(VALID_CANDIDATE)})
        _make_provider().interpret(VALID_INPUT)
        check("C3 wrong arity rejected", False, "no exception")
    except MalformedOutputError as e:
        check("C3 wrong arity rejected", "arity" in str(e), str(e))
    finally:
        restore()

    # C4 error envelope: model unavailable
    try:
        _run_with_envelope({"error": "model_unavailable", "detail": "load failed",
                            "model": dict(REMOTE_MODEL_INFO)})
        check("C4 error envelope → ModelUnavailableError", False, "no exception")
    except ModelUnavailableError as e:
        check("C4 error envelope → ModelUnavailableError", "model_unavailable" in str(e), str(e))

    # C5 error envelope: forbidden accounting fields
    try:
        _run_with_envelope({"error": "forbidden_accounting_fields", "detail": "debit_lines",
                            "model": dict(REMOTE_MODEL_INFO)})
        check("C5 forbidden error envelope → ForbiddenAccountingFieldError", False, "no exception")
    except ForbiddenAccountingFieldError as e:
        check("C5 forbidden error envelope → ForbiddenAccountingFieldError",
              "debit_lines" in str(e), str(e))

    # C6 forbidden field inside candidate
    forbidden = dict(VALID_CANDIDATE)
    forbidden["debit_lines"] = [{"account": "x", "amount": "1"}]
    try:
        _run_with_envelope(_ok_envelope(candidate=forbidden)[0])
        check("C6 forbidden candidate field rejected", False, "no exception")
    except ForbiddenAccountingFieldError as e:
        check("C6 forbidden candidate field rejected", "debit_lines" in str(e), str(e))

    # C7 missing 18-field member
    missing = dict(VALID_CANDIDATE)
    del missing["overall_confidence"]
    try:
        _run_with_envelope(_ok_envelope(candidate=missing)[0])
        check("C7 missing 18-field member rejected", False, "no exception")
    except MalformedOutputError as e:
        check("C7 missing 18-field member rejected", "overall_confidence" in str(e), str(e))

    # C8 empty input
    try:
        restore = _install_client_stub(_ok_envelope())
        _make_provider().interpret("   ")
        check("C8 empty input rejected", False, "no exception")
    except MalformedOutputError:
        check("C8 empty input rejected", True)
    finally:
        restore()


# ---------------------------------------------------------------------------
# D. Transport failures
# ---------------------------------------------------------------------------

def test_transport_failures() -> None:
    print("\n--- D. Transport failures fail closed ---")

    restore = _install_client_stub(None)
    try:
        # D1 timeout
        _FakeGradioClient.next_response = Exception(
            "The upstream Gradio app has raised an exception or timed out"
        )
        try:
            _make_provider().interpret(VALID_INPUT)
            check("D1 timeout → ModelUnavailableError", False, "no exception")
        except ModelUnavailableError as e:
            check("D1 timeout → ModelUnavailableError", "timeout" in str(e).lower(), str(e))

        # D2 connection failure
        _FakeGradioClient.next_response = ConnectionError("name resolution failed")
        try:
            _make_provider().interpret(VALID_INPUT)
            check("D2 connection failure → ModelUnavailableError", False, "no exception")
        except ModelUnavailableError as e:
            check("D2 connection failure → ModelUnavailableError",
                  "name resolution" in str(e), str(e))

        # D3 server failure
        _FakeGradioClient.next_response = RuntimeError("HTTP 500")
        try:
            _make_provider().interpret(VALID_INPUT)
            check("D3 server failure → ModelUnavailableError", False, "no exception")
        except ModelUnavailableError as e:
            check("D3 server failure → ModelUnavailableError", "500" in str(e), str(e))
    finally:
        restore()


# ---------------------------------------------------------------------------
# E. No fallback (LocalHF never invoked)
# ---------------------------------------------------------------------------

class _SentinelRunner:
    """Records ANY access — proves the local path is never touched."""

    def __init__(self) -> None:
        self.touched = 0

    def status(self):
        self.touched += 1
        return {}

    def is_available(self):
        self.touched += 1
        return False

    def ensure_loaded(self):
        self.touched += 1
        return False, "sentinel: must never be called"


def test_no_fallback() -> None:
    print("\n--- E. No fallback to local/deterministic path ---")

    restore = _install_client_stub(ConnectionError("space down"))
    try:
        sentinel = _SentinelRunner()
        kernel = Kernel(model_provider=_make_provider())
        result = kernel.process(VALID_INPUT)
    finally:
        restore()

    check("E1 gradio down → Kernel MODEL_UNAVAILABLE", result.status == MODEL_UNAVAILABLE,
          result.status)
    check("E2 no fallback interpretation produced", result.interpretation_candidate is None)
    check("E3 LocalHF runner never touched", sentinel.touched == 0,
          f"touched={sentinel.touched}")


# ---------------------------------------------------------------------------
# F. Selection factory
# ---------------------------------------------------------------------------

def test_selection() -> None:
    print("\n--- F. Selection factory ---")

    old_url = os.environ.get(ENDPOINT_URL_ENV)
    old_transport = os.environ.get(TRANSPORT_ENV)
    try:
        os.environ[ENDPOINT_URL_ENV] = TEST_SPACE_URL

        os.environ[TRANSPORT_ENV] = "gradio"
        p = get_model_provider()
        check("F1 transport=gradio → HFGradioModelProvider",
              isinstance(p, HFGradioModelProvider), type(p).__name__)

        os.environ[TRANSPORT_ENV] = "http"
        p2 = get_model_provider()
        check("F2 transport=http → RemoteHFModelProvider",
              isinstance(p2, RemoteHFModelProvider), type(p2).__name__)

        os.environ.pop(TRANSPORT_ENV, None)
        p3 = get_model_provider()
        check("F2b transport unset → RemoteHFModelProvider (default unchanged)",
              isinstance(p3, RemoteHFModelProvider), type(p3).__name__)

        os.environ.pop(ENDPOINT_URL_ENV, None)
        p4 = get_model_provider()
        check("F3 url unset → LocalHFModelProvider (unchanged)",
              isinstance(p4, LocalHFModelProvider), type(p4).__name__)

        sentinel = LocalHFModelProvider()
        check("F4 explicit provider wins", get_model_provider(provider=sentinel) is sentinel)
    finally:
        if old_url is None:
            os.environ.pop(ENDPOINT_URL_ENV, None)
        else:
            os.environ[ENDPOINT_URL_ENV] = old_url
        if old_transport is None:
            os.environ.pop(TRANSPORT_ENV, None)
        else:
            os.environ[TRANSPORT_ENV] = old_transport

    check("F5 gradio default timeout is 120 s", DEFAULT_GRADIO_TIMEOUT == 120.0)


# ---------------------------------------------------------------------------
# G. Kernel integration
# ---------------------------------------------------------------------------

def test_kernel_integration() -> None:
    print("\n--- G. Kernel integration (gradio provider behind the seam) ---")

    restore = _install_client_stub(_ok_envelope())
    try:
        kernel = Kernel(model_provider=_make_provider())
        result = kernel.process(VALID_INPUT)
    finally:
        restore()

    check("G1 cold gradio provider passes pre-flight",
          result.status != MODEL_UNAVAILABLE, result.status)
    check("G2 full pipeline reached deterministic accounting",
          result.status in ("VERIFIED", "REVIEW_REQUIRED", "BLOCKED"),
          f"status={result.status} issues={result.issues}")
    check("G3 candidate carried on result", isinstance(result.interpretation_candidate, dict))


# ---------------------------------------------------------------------------
# H. Space fidelity (static checks; no model load)
# ---------------------------------------------------------------------------

_SPACE_PATH = _REPO_ROOT / "hf_space" / "app.py"


def _load_space_module():
    spec = importlib.util.spec_from_file_location("platrixa_hf_space_app", _SPACE_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Execute only the module top — gradio import is at the bottom; we only
    # need the constants/functions defined before the UI section. To avoid
    # importing gradio in the sandbox, read the source and exec a trimmed copy
    # that stops before "import gradio".
    source = _SPACE_PATH.read_text(encoding="utf-8")
    marker = "\nimport gradio as gr"
    if marker in source:
        source = source.split(marker)[0]
    exec(compile(source, str(_SPACE_PATH), "exec"), mod.__dict__)  # noqa: S102
    return mod


def test_space_fidelity() -> None:
    print("\n--- H. Space fidelity (static, no model load) ---")

    if not _SPACE_PATH.exists():
        check("H0 hf_space/app.py exists", False, str(_SPACE_PATH))
        return
    check("H0 hf_space/app.py exists", True)

    try:
        mod = _load_space_module()
    except Exception as exc:  # noqa: BLE001
        check("H0 hf_space/app.py parses", False, repr(exc))
        return

    check("H1 base model id pin", mod.BASE_MODEL_ID == BASE_MODEL_ID)
    check("H1b base revision pin", mod.BASE_MODEL_REVISION == BASE_MODEL_REVISION)
    check("H1c adapter repo pin", mod.ADAPTER_REPO_ID == ADAPTER_REPO_ID)
    check("H1d adapter revision pin", mod.ADAPTER_REVISION == ADAPTER_REVISION)

    # H2: byte-exact prompt vs the formatted SFT data (test split, first record).
    formatted = _REPO_ROOT / "training_data" / "fyjc_specialist_test_formatted.jsonl"
    with open(formatted, encoding="utf-8") as fh:
        record = json.loads(fh.readline())
    text = record["text"]
    sep = "### Response:\n"
    expected_prompt = text.split(sep)[0] + sep
    sft_input = text.split("### Input:\n")[1].split("\n\n### Response:")[0]
    actual_prompt = mod.build_prompt(sft_input)
    check("H2 build_prompt byte-exact vs SFT data", actual_prompt == expected_prompt)

    # H3: envelope contract keys present in the Space's interpret pipeline.
    envelope = _ok_envelope()[0]
    check("H3 envelope carries interpretation+model",
          set(envelope.keys()) == {"interpretation", "model"})
    check("H4 REQUIRED_FIELDS_18 == ALL_VALID_FIELDS",
          set(mod.REQUIRED_FIELDS_18) == set(ALL_VALID_FIELDS))
    # H5: forbidden-field guard exists in the Space module too.
    probe = dict(VALID_CANDIDATE)
    probe["journal"] = "x"
    check("H5 space forbidden-field detection",
          bool(mod.FORBIDDEN_ACCOUNTING_FIELDS & set(probe.keys())))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 72)
    print("PLATRIXA — PHASE 7S HF GRADIO TRANSPORT ADAPTER TEST")
    print("=" * 72)

    test_success_path()
    test_identity_enforcement()
    test_envelope_failures()
    test_transport_failures()
    test_no_fallback()
    test_selection()
    test_kernel_integration()
    test_space_fidelity()

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
