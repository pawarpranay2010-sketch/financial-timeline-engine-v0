#!/usr/bin/env python3
"""Platrixa — direct model → candidate → deterministic validation path.

The worker suite (``scripts/platrixa_outbound_inference_worker_test.py``) proves
the DEVICE half: which endpoint is called, what is sent, and that the model body
is transported verbatim. It cannot prove the SERVER half, because that suite is
deliberately stdlib-only and imports nothing from the repository.

This script closes that gap offline. It drives the REAL worker dispatch
(``OutboundInferenceWorker.call_bridge``, which routes to ``call_model`` when a
loopback model URL is configured) with llama-server-shaped HTTP bodies, then
feeds the resulting body through the REAL shared contract parser
(``backend.model_provider.local_bridge.interpretation_from_bridge_body``) and the
REAL deterministic pipeline (``Kernel.process_from_source``), exactly as
``POST /v1/worker/jobs/{job_id}/result`` does.

What it proves:

    A  a grounded candidate reaches a deterministic terminal state, and the
       journal entry is produced by the KERNEL, not by the model
    B  both dialects (chat / completion) reach the same terminal state
    C  trailing text after the JSON, and a prompt echoed by /v1/completions,
       are handled by extraction — not by trusting the model's framing
    D  a model's ungrounded claim cannot pass: GROUNDING_FAILED, no accounting
    E  a candidate missing a required field cannot pass: VALIDATION_FAILED
    F  a candidate that emits accounting authority is refused: FORBIDDEN_OUTPUT
    G  a truncated generation is refused: VALIDATION_FAILED, no accounting
    H  a model that declares itself VERIFIED is clamped, and identity stays
       device-reported-unverified
    I  text that is not a JSON object is refused: VALIDATION_FAILED
    J  wrong-typed and out-of-enum fields are refused: VALIDATION_FAILED
    K  with direct-model mode OFF the worker still uses the ORIGINAL bridge
       POST and passes its body through verbatim

Every case enters through the production dispatch (``call_bridge``) and the
production two-step server path (``device_reported_model_identity`` +
``interpretation_from_bridge_body`` → ``Kernel.process_from_source``), which is
exactly what ``POST /v1/worker/jobs/{job_id}/result`` calls.

No socket is opened and no live device is required. A live check against the
phone's ``http://127.0.0.1:11434`` remains a separate, device-side step.

Run:
    python scripts/platrixa_direct_model_validation_path_test.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from platrixa_outbound_inference_worker import (  # noqa: E402
    API_KEY_HEADER,
    BRIDGE_KEY_HEADER,
    OutboundInferenceWorker,
    WorkerConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# "cash" is IN the input on purpose: the grounded case must be able to pass.
RAW_INPUT = "purchased furniture from raj for rs.25000 in cash"
# Same transaction, but the input never mentions cash — a model that claims CASH
# here is asserting something the input does not support.
RAW_INPUT_NO_METHOD = "purchased furniture from raj for rs.25000"

MODEL_URL = "http://127.0.0.1:11434"
BRIDGE_URL = "http://127.0.0.1:8081"
TENANT_KEY = "plx_test_tenant_key_not_a_secret"
BRIDGE_KEY = "bridge-test-key-not-a-secret"


def candidate(**overrides):
    """A schema-shaped 18-field candidate (the model's proposal, not truth)."""
    value = {
        "transaction_type": "PURCHASE",
        "parties": [],
        "amounts": [],
        "payment_method": "CASH",
        "references": [],
        "ambiguities": [],
        "grounding": {"all_fields_explicitly_grounded": True, "inferred_fields": []},
        "transaction_type_enum": "PURCHASE",
        "payment_method_enum": "CASH",
        "ambiguity_flags": ["NONE"],
        "referenced_transaction_index": None,
        "referenced_party": None,
        "referenced_amount": None,
        "field_confidences": [],
        "overall_confidence": "0.40",
        "suggested_status": "REVIEW_REQUIRED",
        "safety_flags": ["NONE"],
        "scope_flags": ["SINGLE_TRANSACTION"],
    }
    value.update(overrides)
    return value


class FakeResponse:
    """The subset of ``requests.Response`` the worker's model path reads."""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return json.loads(self.text)


class FakeSession:
    """A fake transport: records the call, returns the scripted body."""

    def __init__(self, text: str, status_code: int = 200) -> None:
        self._text = text
        self._status_code = status_code
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors requests
        self.calls.append({"url": url, "json": json, "headers": dict(headers or {}), "timeout": timeout})
        return FakeResponse(self._status_code, self._text)


class RaisingSession:
    """A transport that must never be used on the path under test."""

    def __init__(self) -> None:
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors requests
        self.calls.append(url)
        raise AssertionError(f"unexpected POST to {url}")


def chat_body(content: str, finish_reason: str = "stop") -> str:
    return json.dumps({
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "default",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
    })


def completion_body(text: str, finish_reason: str = "stop") -> str:
    return json.dumps({
        "id": "cmpl-test",
        "object": "text_completion",
        "model": "default",
        "choices": [{"index": 0, "text": text, "finish_reason": finish_reason}],
    })


def device_body(answer: str, *, dialect: str = "completion", finish_reason: str = "stop", **extra):
    """Run the REAL worker dispatch and return (bridge_body, model_call).

    The entry point is ``call_bridge`` — the production dispatch the job loop
    uses (``process_job`` → ``call_bridge``). With ``model_url`` configured it
    must route to the direct model and never touch the device bridge, so the
    bridge transport is a raising stub: a fallback would fail loudly instead of
    silently proving the wrong path.
    """
    wire = completion_body(answer, finish_reason) if dialect == "completion" else chat_body(answer, finish_reason)
    model_session = FakeSession(wire)
    bridge_session = RaisingSession()
    worker = OutboundInferenceWorker(
        WorkerConfig(
            api_base_url="https://api.example.test",
            api_key=TENANT_KEY,
            worker_id="device-a",
            model_url=MODEL_URL,
            model_dialect=dialect,
        ),
        session=FakeSession("{}"),
        bridge_session=bridge_session,
        model_session=model_session,
    )
    body = worker.call_bridge(RAW_INPUT)
    body.update(extra)
    assert len(model_session.calls) == 1, f"expected one model POST, got {model_session.calls}"
    assert bridge_session.calls == [], "the direct-model path must not fall back to the device bridge"
    return body, model_session.calls[0]


def run_kernel(body, raw_input: str = RAW_INPUT):
    """body → shared parser → deterministic kernel (the route's own two steps).

    Mirrors ``POST /v1/worker/jobs/{job_id}/result``: the device-reported
    identity is read FROM THE BODY (never pinned here), then the shared parser
    builds the interpretation and the kernel runs it deterministically.
    """
    from backend.kernel.kernel import Kernel
    from backend.model_provider.local_bridge import (
        device_reported_model_identity,
        interpretation_from_bridge_body,
    )

    model_id, provider_revision = device_reported_model_identity(body)

    def interpret():
        return interpretation_from_bridge_body(
            body,
            raw_input,
            model_id=model_id,
            provider_revision=provider_revision,
        )

    return Kernel().process_from_source(raw_input, interpret)


def balanced(result) -> bool:
    accounting = result.accounting_result or {}
    debits = sum(line["amount"] for line in accounting.get("debit_lines") or [])
    credits = sum(line["amount"] for line in accounting.get("credit_lines") or [])
    return bool(accounting.get("debit_lines")) and debits == credits


# ---------------------------------------------------------------------------
# A-B  the grounded happy path, both dialects
# ---------------------------------------------------------------------------


def test_grounded_completion_reaches_kernel_verified() -> None:
    """A completion-dialect answer with trailing prose → VERIFIED by the kernel."""
    answer = json.dumps(candidate()) + "\n\nExplanation: furniture bought for the shop."
    body, call = device_body(answer, dialect="completion")

    assert call["url"] == f"{MODEL_URL}/v1/completions", call["url"]
    assert call["json"]["prompt"].rstrip().endswith("### Response:"), "raw Alpaca prompt, no chat turns"
    assert "messages" not in call["json"]
    assert body["interpretation_text"] == answer, "the model text must be transported verbatim"

    result = run_kernel(body)
    assert result.status == "VERIFIED", f"unexpected status {result.status}: {result.issues}"
    assert result.issues == []
    assert result.verification_status == "GROUNDED", result.grounding_issues
    assert balanced(result), f"the kernel must produce a balanced entry: {result.accounting_result}"


def test_chat_dialect_reaches_the_same_terminal_state() -> None:
    """The chat dialect carries the same prompt and the same answer → same state."""
    body, call = device_body(json.dumps(candidate()), dialect="chat")

    assert call["url"] == f"{MODEL_URL}/v1/chat/completions", call["url"]
    assert call["json"]["messages"] == [{"role": "user", "content": call["json"]["messages"][0]["content"]}]
    assert call["json"]["messages"][0]["content"].startswith("Below is an instruction"), "Alpaca framing kept"

    result = run_kernel(body)
    assert result.status == "VERIFIED", f"unexpected status {result.status}: {result.issues}"
    assert balanced(result)


# ---------------------------------------------------------------------------
# C  the observed phone behaviour: prompt echo + trailing text
# ---------------------------------------------------------------------------


def test_prompt_echo_and_trailing_text_still_extract_the_candidate() -> None:
    """The phone echoed the prompt and appended prose; extraction still works.

    The worker transports the whole text (it must not guess where the prompt
    ends); the SERVER's parser is what extracts the JSON candidate, and it does
    so without repairing or inventing a field.
    """
    prompt = "Below is an instruction that describes a task, paired with an input that provides further context."

    _, call = device_body(json.dumps(candidate()), dialect="completion")
    echoed_text = call["json"]["prompt"] + json.dumps(candidate()) + "\n### end"
    echo_body, _ = device_body(echoed_text, dialect="completion")

    assert echo_body["interpretation_text"].startswith(prompt)
    assert echo_body["interpretation_text"] == echoed_text, "no silent truncation of the echo"

    result = run_kernel(echo_body)
    assert result.status == "VERIFIED", f"unexpected status {result.status}: {result.issues}"
    assert result.interpretation_candidate is not None
    assert set(result.interpretation_candidate) == set(candidate()), "no field added or dropped"


# ---------------------------------------------------------------------------
# D-G  the firewall: a model claim never becomes financial truth on its own
# ---------------------------------------------------------------------------


def test_ungrounded_claim_cannot_pass() -> None:
    """The model asserts CASH; the input never says cash → GROUNDING_FAILED."""
    body, _ = device_body(json.dumps(candidate()), dialect="completion")
    result = run_kernel(body, raw_input=RAW_INPUT_NO_METHOD)

    assert result.status == "GROUNDING_FAILED", f"unexpected status {result.status}: {result.issues}"
    assert result.accounting_result in (None, {}), "no accounting may exist for an ungrounded claim"
    assert any("cash" in issue.lower() for issue in result.issues), result.issues


def test_missing_required_field_cannot_pass() -> None:
    """17 of 18 fields → VALIDATION_FAILED; the missing field is named."""
    incomplete = candidate()
    del incomplete["suggested_status"]
    body, _ = device_body(json.dumps(incomplete), dialect="completion")
    result = run_kernel(body)

    assert result.status == "VALIDATION_FAILED", f"unexpected status {result.status}: {result.issues}"
    assert any("suggested_status" in issue for issue in result.issues), result.issues
    assert result.accounting_result in (None, {})


def test_model_emitting_accounting_authority_is_refused() -> None:
    """A model that writes debit lines itself is refused: FORBIDDEN_OUTPUT."""
    body, _ = device_body(json.dumps(candidate(debit_lines=[{"account": "Furniture", "amount": 25000}])), dialect="completion")
    result = run_kernel(body)

    assert result.status == "FORBIDDEN_OUTPUT", f"unexpected status {result.status}: {result.issues}"
    assert any("debit_lines" in issue for issue in result.issues), result.issues
    assert result.accounting_result in (None, {}), "the model's own accounting must never be adopted"


def test_malformed_candidate_json_fails_closed() -> None:
    """Model text that is not a JSON object → VALIDATION_FAILED, nothing parsed."""
    body, _ = device_body("Sure! Here is the JSON you asked for.", dialect="completion")
    result = run_kernel(body)

    assert result.status == "VALIDATION_FAILED", f"unexpected status {result.status}: {result.issues}"
    assert any("not valid JSON" in issue for issue in result.issues), result.issues
    assert result.accounting_result in (None, {}), "no accounting may exist for unparseable output"


def test_invalid_required_field_cannot_pass() -> None:
    """A wrong-typed and an out-of-enum field each fail schema validation."""
    for overrides, expected in (
        ({"amounts": "25000"}, "type_mismatch"),
        ({"payment_method_enum": "NOT_A_METHOD"}, "invalid_enum_value"),
    ):
        body, _ = device_body(json.dumps(candidate(**overrides)), dialect="completion")
        result = run_kernel(body)

        assert result.status == "VALIDATION_FAILED", (
            f"{overrides} → unexpected status {result.status}: {result.issues}"
        )
        assert any("schema validation" in issue and expected in issue for issue in result.issues), result.issues
        assert result.accounting_result in (None, {}), f"{overrides} must not produce accounting"


def test_truncated_generation_cannot_pass() -> None:
    """finish_reason "length" → TRUNCATED → VALIDATION_FAILED, never parsed."""
    body, _ = device_body('{"transaction_type": "PUR', dialect="completion", finish_reason="length")
    assert body["completion_status"] == "TRUNCATED"

    result = run_kernel(body)
    assert result.status == "VALIDATION_FAILED", f"unexpected status {result.status}: {result.issues}"
    assert any("incomplete model output" in issue for issue in result.issues), result.issues
    assert result.accounting_result in (None, {})


# ---------------------------------------------------------------------------
# H  the model is never the authority for status or identity
# ---------------------------------------------------------------------------


def test_model_cannot_declare_itself_verified() -> None:
    """suggested_status VERIFIED is clamped before the kernel sees it."""
    body, _ = device_body(json.dumps(candidate(suggested_status="VERIFIED")), dialect="completion")
    result = run_kernel(body)

    assert result.interpretation_candidate is not None
    assert result.interpretation_candidate["suggested_status"] == "REVIEW_REQUIRED", (
        "the model may never declare a trusted final status"
    )
    evidence = result.evidence
    assert evidence is not None, "a validated result must carry execution evidence"
    assert evidence.model_identity["identity_source"] == "device-reported-unverified"
    assert evidence.model_identity["provider_revision"] == "PROVENANCE_UNKNOWN", (
        "the device GGUF provenance must stay unknown; it may not be borrowed"
    )
    # The route reads identity from the body's own ``model`` block, so the id is
    # the provider string android-llama-server reported — never a pinned name.
    assert evidence.model_identity["model_id"] == "android-llama-server"


# ---------------------------------------------------------------------------
# K  direct-model mode is additive: the original bridge path is untouched
# ---------------------------------------------------------------------------


def test_bridge_mode_is_unchanged_when_direct_model_is_disabled() -> None:
    """No model_url → the ORIGINAL bridge POST, body passed through verbatim."""
    bridge_wire = {
        "interpretation_text": json.dumps(candidate()),
        "completion_status": "COMPLETE",
        "finish_reason": "stop",
        "model": {
            "provider": "llama.cpp local bridge",
            "artifact_identity": "PROVENANCE_UNKNOWN",
        },
    }
    bridge_session = FakeSession(json.dumps(bridge_wire))
    model_session = FakeSession("{}")
    worker = OutboundInferenceWorker(
        WorkerConfig(
            api_base_url="https://api.example.test",
            api_key=TENANT_KEY,
            worker_id="device-a",
            bridge_url=BRIDGE_URL,
            bridge_key=BRIDGE_KEY,
            # model_url deliberately unset: direct-model mode is OFF
        ),
        session=FakeSession("{}"),
        bridge_session=bridge_session,
        model_session=model_session,
    )

    body = worker.call_bridge(RAW_INPUT)

    assert body == bridge_wire, "the bridge body must be passed through verbatim"
    assert model_session.calls == [], "the model endpoint must not be used when it is not configured"
    assert len(bridge_session.calls) == 1, bridge_session.calls
    call = bridge_session.calls[0]
    assert call["url"] == f"{BRIDGE_URL}/interpret", call["url"]
    assert call["json"] == {"text": RAW_INPUT}, call["json"]
    assert call["headers"][BRIDGE_KEY_HEADER] == BRIDGE_KEY, call["headers"]
    assert API_KEY_HEADER not in call["headers"], "the tenant API key must never reach the bridge"
    assert "Authorization" not in call["headers"], "no model credential belongs on the bridge call"

    result = run_kernel(body)
    assert result.status == "VERIFIED", f"unexpected status {result.status}: {result.issues}"
    assert balanced(result)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = (
    test_grounded_completion_reaches_kernel_verified,
    test_chat_dialect_reaches_the_same_terminal_state,
    test_prompt_echo_and_trailing_text_still_extract_the_candidate,
    test_ungrounded_claim_cannot_pass,
    test_missing_required_field_cannot_pass,
    test_invalid_required_field_cannot_pass,
    test_model_emitting_accounting_authority_is_refused,
    test_truncated_generation_cannot_pass,
    test_malformed_candidate_json_fails_closed,
    test_model_cannot_declare_itself_verified,
    test_bridge_mode_is_unchanged_when_direct_model_is_disabled,
)


def main() -> int:
    print("=" * 78)
    print("Direct model → candidate → deterministic validation (offline)")
    print("=" * 78)
    passed = 0
    failures = []
    for fn in TESTS:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - a failing test is data, not a crash
            failures.append((fn.__name__, exc))
            print(f"  [FAIL] {fn.__name__} — {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"  [PASS] {fn.__name__}")
    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{len(TESTS)} checks passed")
    print("=" * 78)
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
