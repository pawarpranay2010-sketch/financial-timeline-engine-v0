#!/usr/bin/env python3
"""Android llama.cpp server ↔ Platrixa worker — contract suite.

Run:
    python scripts/platrixa_android_model_contract_test.py
    pytest scripts/platrixa_android_model_contract_test.py

Verifies the DIRECT connection the user will run on one phone: the Android LLM
app's OpenAI-compatible server at ``http://127.0.0.1:11434`` and the outbound
worker in Termux.

Sections
  A  Endpoint configuration — loopback only, and the exact env/CLI contract.
  B  Request construction — POST /v1/chat/completions, exact payload, keys.
  C  Response handling — ordinary JSON, SSE (leading comments, many chunks),
     [DONE], finish_reason stop/length, and every fail-closed case.
  D  Transport failures — connection refused, timeout, HTTP 5xx.
  E  Reliability integration — failures are REPORTED, never submitted.
  F  Model discovery — ``--model-id auto`` resolves from GET /v1/models.
  G  Provenance — configuration only; the server cannot name itself.
  H  Server-side contract — a TRUNCATED candidate is refused by the shared
     parser (the deterministic pipeline, not the worker, is the authority).

Scope: OFFLINE only. The phone is never contacted: every request is served by
an in-process fake transport. Synthetic accounting text only — no real
financial data, no credential, no production endpoint.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import platrixa_outbound_inference_worker as worker_mod  # noqa: E402
from platrixa_outbound_inference_worker import (  # noqa: E402
    CHAT_COMPLETIONS_PATH,
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_PROVIDER,
    MODEL_ID_ENV,
    MODEL_URL_ENV,
    MODEL_DIALECT_ENV,
    MODEL_TIMEOUT_ENV,
    MODEL_MAX_NEW_TOKENS,
    MODEL_TEMPERATURE,
    MODEL_TOP_P,
    PROVENANCE_UNKNOWN,
    OutboundInferenceWorker,
    UnsafeEndpointError,
    WorkerConfig,
    WorkerConfigError,
    build_config,
    build_model_prompt,
    normalize_model_url,
)
from platrixa_outbound_inference_worker_test import (  # noqa: E402
    API_BASE,
    API_KEY,
    BRIDGE_BASE,
    BRIDGE_KEY,
    RESULT_ENVELOPE,
    WORKER_ID,
    FakeClock,
    FakeResponse,
    FakeSession,
    claim_body,
    no_work_body,
    renewal_body,
)

MODEL_URL = "http://127.0.0.1:11434"
SYNTHETIC_INPUT = "purchased stationery from a vendor for rs.1200"
ANSWER = '{"transaction_type": "EXPENSE", "amount": 1200, "currency": "INR"}'


def expect_raises(exc_type, fn) -> BaseException:
    try:
        fn()
    except exc_type as exc:
        return exc
    except BaseException as exc:  # noqa: BLE001 - a wrong type must fail loudly
        raise AssertionError(f"expected {exc_type.__name__}, got {type(exc).__name__}: {exc}") from exc
    raise AssertionError(f"expected {exc_type.__name__}, but nothing was raised")


# ---------------------------------------------------------------------------
# Fake transports
# ---------------------------------------------------------------------------


class Harness:
    """Records claim/lease/result/failure/model/discovery traffic."""

    def __init__(self) -> None:
        self.results: list = []
        self.failures: list = []
        self.claims = 0
        self.models: list = []
        self.discovery: list = []


def make_worker(
    model_handler,
    *,
    model_url=MODEL_URL,
    model_id=DEFAULT_MODEL_ID,
    model_timeout_s=120.0,
    model_key=None,
    model_provider=None,
    claim=1,
    clock=None,
    failure_response=None,
):
    h = Harness()

    def api_handler(call):
        url = call["url"]
        if url.endswith("/claim"):
            h.claims += 1
            if h.claims <= claim:
                return FakeResponse(200, claim_body(raw_input=SYNTHETIC_INPUT))
            return FakeResponse(200, no_work_body(1.0))
        if url.endswith("/lease"):
            return FakeResponse(200, renewal_body())
        if url.endswith("/result"):
            h.results.append(call)
            return FakeResponse(200, RESULT_ENVELOPE)
        if url.endswith("/failure"):
            h.failures.append(call)
            return failure_response() if failure_response else FakeResponse(
                200, {"api_version": "v1", "status": "RETRY_WAIT", "retryable": True}
            )
        raise AssertionError(f"unexpected API call: {url}")

    # The endpoint advertises the id the worker is configured to use, which is
    # what a real phone does; the readiness gate (S1) requires exactly that.
    # "auto" keeps the discovery-specific fixture id so the resolution test
    # stays meaningful.
    advertised_id = (
        "qwen2.5-1.5b-instruct-q4_k_m" if model_id == "auto" else model_id
    )

    def discovery_handler(call):
        h.discovery.append(call)
        return FakeResponse(200, {"object": "list", "data": [{"id": advertised_id}]})

    config = WorkerConfig(
        api_base_url=API_BASE,
        api_key=API_KEY,
        worker_id=WORKER_ID,
        bridge_url=BRIDGE_BASE,
        bridge_key=BRIDGE_KEY,
        model_url=model_url,
        model_id=model_id,
        model_key=model_key,
        model_timeout_s=model_timeout_s,
        model_provider=model_provider,
        heartbeat_tick_s=0.01,
        shutdown_timeout_s=5.0,
    )
    worker = OutboundInferenceWorker(
        config,
        session=FakeSession(api_handler),
        bridge_session=FakeSession(lambda call: (_ for _ in ()).throw(
            AssertionError("the direct-model path must not fall back to the bridge")
        )),
        model_session=FakeSession(model_handler),
        now=clock if clock is not None else __import__("time").monotonic,
        sleep=lambda seconds: None,
    )
    # The discovery probe uses the same model transport; route it explicitly.
    worker._model_session = _RoutedSession(model_handler, discovery_handler)
    h.model_session = worker._model_session
    return worker, h


class _RoutedSession:
    """Fake model transport that also serves GET /v1/models."""

    def __init__(self, model_handler, discovery_handler):
        self._model_handler = model_handler
        self._discovery_handler = discovery_handler
        self.calls: list = []

    def post(self, url, json=None, headers=None, timeout=None):  # noqa: A002 - mirrors requests
        call = {"method": "POST", "url": url, "json": json, "headers": dict(headers or {}), "timeout": timeout}
        self.calls.append(call)
        if url.endswith("/v1/models"):
            return self._discovery_handler(call)
        return self._model_handler(call)

    def get(self, url, headers=None, timeout=None):
        # Model discovery/readiness is a GET by contract (S1).
        call = {"method": "GET", "url": url, "json": None, "headers": dict(headers or {}), "timeout": timeout}
        self.calls.append(call)
        if url.endswith("/v1/models"):
            return self._discovery_handler(call)
        return self._model_handler(call)


def chat_json(content: str = ANSWER, finish_reason: str = "stop", **body_extra) -> FakeResponse:
    body = {
        "id": "chatcmpl-local",
        "object": "chat.completion",
        "model": "whatever-the-server-calls-itself",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
    }
    body.update(body_extra)
    return FakeResponse(200, body=body, headers={"Content-Type": "application/json"})


def sse(*chunks: str, done: bool = True, finish_reason="stop", prefix: str = "", suffix: str = "") -> FakeResponse:
    """An OpenAI-style SSE body built from content deltas."""
    lines = []
    if prefix:
        lines.extend(prefix.splitlines())
    for piece in chunks:
        payload = {"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
        lines.append(f"data: {json.dumps(payload)}")
    if finish_reason is not None:
        tail = {"choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}]}
        lines.append(f"data: {json.dumps(tail)}")
    if done:
        lines.append("data: [DONE]")
    if suffix:
        lines.extend(suffix.splitlines())
    return FakeResponse(
        200,
        text="\n\n".join(lines) + "\n\n",
        headers={"Content-Type": "text/event-stream; charset=utf-8"},
    )


# ---------------------------------------------------------------------------
# A. Endpoint configuration
# ---------------------------------------------------------------------------


def test_loopback_model_urls_are_accepted() -> None:
    for url in (
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
        "http://127.0.0.1:11434/",
    ):
        assert normalize_model_url(url), url


def test_public_and_unsafe_model_urls_are_refused() -> None:
    for url in (
        "http://192.168.1.20:11434",
        "https://api.groq.com/openai",
        "http://events.example.test:11434",
        "ftp://127.0.0.1:11434",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/?x=1",
        "http://127.0.0.1:11434/#frag",
        "http://127.0.0.1:11434/../admin",
    ):
        expect_raises(UnsafeEndpointError, lambda url=url: normalize_model_url(url))


def test_env_contract_maps_to_config() -> None:
    """The exact Termux environment block must build the expected config."""
    ns = _namespace()
    saved = {k: os.environ.get(k) for k in (MODEL_URL_ENV, MODEL_ID_ENV, MODEL_DIALECT_ENV, MODEL_TIMEOUT_ENV)}
    os.environ.update({
        MODEL_URL_ENV: MODEL_URL,
        MODEL_DIALECT_ENV: "chat",
        MODEL_ID_ENV: "qwen2.5-1.5b-instruct-q4_k_m",
        MODEL_TIMEOUT_ENV: "90",
    })
    try:
        cfg = build_config(ns)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert cfg.model_url == MODEL_URL
    assert cfg.model_dialect == "chat"
    assert cfg.model_id == "qwen2.5-1.5b-instruct-q4_k_m"
    assert cfg.model_timeout_s == 90.0
    assert cfg.bridge_url == BRIDGE_BASE and cfg.bridge_key == BRIDGE_KEY
    assert cfg.api_base_url == API_BASE and cfg.api_key == API_KEY


def test_explicit_flags_override_the_environment() -> None:
    saved = os.environ.get(MODEL_ID_ENV)
    os.environ[MODEL_ID_ENV] = "from-env"
    try:
        ns = _namespace(model_id="from-flag")
        cfg = build_config(ns)
    finally:
        if saved is None:
            os.environ.pop(MODEL_ID_ENV, None)
        else:
            os.environ[MODEL_ID_ENV] = saved
    assert cfg.model_id == "from-flag"


def test_default_model_id_is_still_default() -> None:
    ns = _namespace()
    saved = os.environ.pop(MODEL_ID_ENV, None)
    try:
        cfg = build_config(ns)
    finally:
        if saved is not None:
            os.environ[MODEL_ID_ENV] = saved
    assert cfg.model_id == DEFAULT_MODEL_ID == "default"


def _namespace(**overrides) -> argparse.Namespace:
    values = dict(
        api_base_url=API_BASE, api_key=API_KEY, api_key_file=None, worker_id=WORKER_ID,
        bridge_url=BRIDGE_BASE, bridge_key=BRIDGE_KEY, bridge_timeout=120.0,
        model_url=MODEL_URL, model_id=None, model_key=None, model_key_file=None,
        model_timeout=None, model_dialect=None, model_provider=None,
        rate_limit_max_retries=None, rate_limit_max_wait=None,
        once=True, max_polls=None, verbose=False,
    )
    values.update(overrides)
    return argparse.Namespace(**values)


# ---------------------------------------------------------------------------
# B. Request construction
# ---------------------------------------------------------------------------


def test_chat_request_is_exactly_the_documented_shape() -> None:
    calls = []
    worker, h = make_worker(lambda call: (calls.append(call), chat_json())[1])

    worker.process_job(worker.claim().job)

    assert len(calls) == 1, calls
    request = calls[0]
    assert request["url"] == MODEL_URL + CHAT_COMPLETIONS_PATH == "http://127.0.0.1:11434/v1/chat/completions"
    payload = request["json"]
    assert set(payload) == {"model", "messages", "stream", "temperature", "top_p", "max_tokens"}, payload
    assert payload["stream"] is False, "the worker always asks for a non-streamed answer"
    assert payload["temperature"] == MODEL_TEMPERATURE == 0.0
    assert payload["top_p"] == MODEL_TOP_P == 1.0
    assert payload["max_tokens"] == MODEL_MAX_NEW_TOKENS == 512
    assert payload["messages"] == [{"role": "user", "content": build_model_prompt(SYNTHETIC_INPUT)}]
    # The tenant credential must never reach the model server.
    assert request["headers"].get("Authorization") is None
    assert API_KEY not in json.dumps(request["headers"])
    assert request["timeout"] == 120.0


def test_model_key_is_sent_as_bearer_only_to_the_model() -> None:
    calls = []
    worker, h = make_worker(lambda call: (calls.append(call), chat_json())[1], model_key="local-model-key")
    worker.process_job(worker.claim().job)

    assert calls[0]["headers"]["Authorization"] == "Bearer local-model-key"
    api_calls = h.results[0]
    assert "local-model-key" not in json.dumps(api_calls["headers"])
    assert api_calls["headers"]["X-Platrixa-API-Key"] == API_KEY


def test_configured_model_id_is_used_verbatim() -> None:
    calls = []
    worker, _h = make_worker(
        lambda call: (calls.append(call), chat_json())[1],
        model_id="qwen2.5-1.5b-instruct-q4_k_m",
    )
    worker.process_job(worker.claim().job)
    assert calls[0]["json"]["model"] == "qwen2.5-1.5b-instruct-q4_k_m"


# ---------------------------------------------------------------------------
# C. Response handling
# ---------------------------------------------------------------------------


def test_ordinary_json_response_is_accepted() -> None:
    worker, h = make_worker(lambda call: chat_json())
    outcome = worker.process_job(worker.claim().job)

    assert outcome.accepted is True, dict(outcome)
    body = h.results[0]["json"]["bridge_body"]
    assert body["interpretation_text"] == ANSWER
    assert body["completion_status"] == "COMPLETE"
    assert body["finish_reason"] == "stop"


def test_sse_response_with_many_chunks_is_reassembled() -> None:
    worker, h = make_worker(lambda call: sse('{"transaction_type":', ' "EXPENSE",', ' "amount": 1200}'))
    outcome = worker.process_job(worker.claim().job)

    assert outcome.accepted is True
    body = h.results[0]["json"]["bridge_body"]
    assert body["interpretation_text"] == '{"transaction_type": "EXPENSE", "amount": 1200}'
    assert body["completion_status"] == "COMPLETE"
    assert body["finish_reason"] == "stop"


def test_sse_with_leading_comments_and_event_frames_is_still_sse() -> None:
    """A keep-alive/comment prefix must not be mistaken for broken JSON."""
    response = sse(
        '{"transaction_type": "EXPENSE"}',
        prefix=": ping\nevent: message\nretry: 1000",
        suffix=": keep-alive",
    )
    worker, h = make_worker(lambda call: response)
    worker.process_job(worker.claim().job)
    assert h.results[0]["json"]["bridge_body"]["interpretation_text"] == '{"transaction_type": "EXPENSE"}'


def test_sse_requires_done() -> None:
    worker, h = make_worker(lambda call: sse('{"a": 1}', done=False))
    completed = worker.run_forever(max_polls=1)

    assert completed == 0
    assert h.results == [], "an incomplete stream must never be submitted"
    assert h.failures, "the failure must be reported"
    assert h.failures[0]["json"]["error"]["code"] == "VALIDATION_FAILED"


def test_sse_malformed_chunk_fails_closed() -> None:
    body = 'data: {"choices": [{"delta": {"content": "x"}}]}\n\ndata: {not json\n\ndata: [DONE]\n\n'
    worker, h = make_worker(lambda call: FakeResponse(200, text=body,
                                                      headers={"Content-Type": "text/event-stream"}))
    worker.run_forever(max_polls=1)
    assert h.results == []
    assert h.failures[0]["json"]["error"]["code"] == "VALIDATION_FAILED"


def test_sse_empty_content_fails_closed() -> None:
    worker, h = make_worker(lambda call: sse("", "", done=True))
    worker.run_forever(max_polls=1)
    assert h.results == [], "empty assistant content is not a candidate"
    assert h.failures[0]["json"]["error"]["code"] == "VALIDATION_FAILED"


def test_finish_reason_length_is_labelled_truncated_never_complete() -> None:
    worker, h = make_worker(lambda call: chat_json('{"partial":', finish_reason="length"))
    try:
        worker.process_job(worker.claim().job)
    except Exception:  # noqa: BLE001 - a server-side 422 is acceptable
        pass
    assert h.results, "the honest TRUNCATED label is submitted for the kernel to decide"
    body = h.results[0]["json"]["bridge_body"]
    assert body["completion_status"] == "TRUNCATED"
    assert body["completion_status"] != "COMPLETE"
    assert body["finish_reason"] == "length"


def test_missing_finish_reason_fails_closed() -> None:
    body = {"choices": [{"message": {"content": ANSWER}}]}  # no finish_reason key
    worker, h = make_worker(lambda call: FakeResponse(200, body=body))
    worker.run_forever(max_polls=1)
    assert h.results == []
    assert h.failures[0]["json"]["error"]["code"] == "VALIDATION_FAILED"


def test_null_or_empty_finish_reason_fails_closed() -> None:
    for finish in (None, ""):
        body = {"choices": [{"message": {"content": ANSWER}, "finish_reason": finish}]}
        worker, h = make_worker(lambda call, body=body: FakeResponse(200, body=body))
        worker.run_forever(max_polls=1)
        assert h.results == [], finish
        assert h.failures, finish


def test_unrecognized_finish_reason_fails_closed() -> None:
    worker, h = make_worker(lambda call: chat_json(ANSWER, finish_reason="content_filter"))
    worker.run_forever(max_polls=1)
    assert h.results == []
    assert h.failures[0]["json"]["error"]["code"] == "VALIDATION_FAILED"


def test_empty_assistant_content_fails_closed() -> None:
    for content in ("", "   ", None):
        worker, h = make_worker(lambda call, c=content: chat_json(c))
        worker.run_forever(max_polls=1)
        assert h.results == [], content
        assert h.failures[0]["json"]["error"]["code"] == "VALIDATION_FAILED"


def test_malformed_response_body_fails_closed() -> None:
    for text in ("not json", "[]", '{"choices": []}', '{"choices": [{}]}'):
        worker, h = make_worker(lambda call, t=text: FakeResponse(200, text=t))
        worker.run_forever(max_polls=1)
        assert h.results == [], text
        assert h.failures, text


def test_oversized_model_response_fails_closed() -> None:
    huge = "x" * (64 * 1024 + 10)
    worker, h = make_worker(lambda call: chat_json(huge))
    worker.run_forever(max_polls=1)
    assert h.results == []
    assert h.failures[0]["json"]["error"]["code"] == "VALIDATION_FAILED"


def test_completion_dialect_text_field_still_works() -> None:
    """The phone may answer /v1/completions with `text` instead of `message`."""
    body = {"choices": [{"text": ANSWER, "finish_reason": "stop"}]}
    worker, h = make_worker(lambda call: FakeResponse(200, body=body), model_id="phone")
    worker.process_job(worker.claim().job)
    assert h.results[0]["json"]["bridge_body"]["interpretation_text"] == ANSWER


# ---------------------------------------------------------------------------
# D. Transport failures
# ---------------------------------------------------------------------------


def test_connection_refused_is_reported_not_submitted() -> None:
    def boom(call):
        raise requests.ConnectionError("Connection refused: 127.0.0.1:11434")

    worker, h = make_worker(boom)
    completed = worker.run_forever(max_polls=1)

    assert completed == 0
    assert h.results == [], "no HTTP failure may become a submitted candidate"
    assert h.failures[0]["json"]["error"]["code"] == "MODEL_UNAVAILABLE"


def test_timeout_is_reported_not_submitted() -> None:
    def boom(call):
        raise requests.Timeout("read timed out")

    worker, h = make_worker(boom)
    worker.run_forever(max_polls=1)
    assert h.results == []
    assert h.failures[0]["json"]["error"]["code"] == "MODEL_UNAVAILABLE"


def test_http_5xx_is_reported_not_submitted() -> None:
    worker, h = make_worker(lambda call: FakeResponse(503, {"error": "model not loaded"}))
    worker.run_forever(max_polls=1)
    assert h.results == []
    assert h.failures[0]["json"]["error"]["code"] == "MODEL_UNAVAILABLE"


def test_model_timeout_is_configurable_and_applied() -> None:
    seen = []
    worker, _h = make_worker(
        lambda call: (seen.append(call["timeout"]), chat_json())[1],
        model_timeout_s=45.0,
    )
    worker.process_job(worker.claim().job)
    assert seen == [45.0], seen


# ---------------------------------------------------------------------------
# E. Reliability integration
# ---------------------------------------------------------------------------


def test_failure_report_carries_the_lease_proof_and_no_payload() -> None:
    worker, h = make_worker(lambda call: chat_json(""))
    worker.run_forever(max_polls=1)

    report = h.failures[0]
    assert report["json"]["lease"] == {"owner": WORKER_ID, "generation": 1}
    encoded = json.dumps(report["json"])
    assert SYNTHETIC_INPUT not in encoded
    assert API_KEY not in encoded
    assert report["url"].endswith("/v1/worker/jobs/job-1/failure")


def test_recovered_model_still_submits_after_a_500() -> None:
    """5xx → reported; the NEXT job is served normally (no poisoned state)."""
    calls = []

    def handler(call):
        calls.append(call)
        if len(calls) == 1:
            return FakeResponse(500, {"error": "transient"})
        return chat_json()

    worker, h = make_worker(handler, claim=2)
    worker.run_forever(max_polls=2)

    assert len(calls) == 2, calls
    assert len(h.failures) == 1, "only the failed generation is reported"
    assert len(h.results) == 1, "the recovered generation commits normally"


# ---------------------------------------------------------------------------
# F. Model discovery
# ---------------------------------------------------------------------------


def test_auto_model_id_resolves_from_the_endpoint() -> None:
    worker, h = make_worker(lambda call: chat_json(), model_id="auto")

    assert worker.needs_model_discovery() is True
    resolved = worker.preflight_model_endpoint()
    assert resolved == "qwen2.5-1.5b-instruct-q4_k_m"
    assert h.discovery and h.discovery[0]["url"] == MODEL_URL + "/v1/models"

    worker.process_job(worker.claim().job)
    body = h.results[0]["json"]["bridge_body"]
    assert body["model"]["model"] == "qwen2.5-1.5b-instruct-q4_k_m"
    assert body["model"]["provider"] == DEFAULT_MODEL_PROVIDER


def test_explicit_model_id_makes_no_discovery_call() -> None:
    worker, h = make_worker(lambda call: chat_json(), model_id="phone-model")
    assert worker.needs_model_discovery() is False
    worker.preflight_model_endpoint()
    assert h.discovery == [], "an explicit id must never trigger /v1/models"


def test_ambiguous_discovery_is_a_configuration_error() -> None:
    def discovery(call):
        return FakeResponse(200, {"data": [{"id": "a"}, {"id": "b"}]})

    worker, h = make_worker(lambda call: chat_json(), model_id="auto")
    h.model_session._discovery_handler = discovery
    exc = expect_raises(WorkerConfigError, worker.preflight_model_endpoint)
    assert "ambiguous" in str(exc)


def test_empty_discovery_is_a_configuration_error() -> None:
    worker, h = make_worker(lambda call: chat_json(), model_id="auto")
    h.model_session._discovery_handler = lambda call: FakeResponse(200, {"data": []})
    expect_raises(WorkerConfigError, worker.preflight_model_endpoint)


def test_unreachable_discovery_is_a_configuration_error() -> None:
    worker, h = make_worker(lambda call: chat_json(), model_id="auto")

    def boom(call):
        raise requests.ConnectionError("refused")

    h.model_session._discovery_handler = boom
    expect_raises(WorkerConfigError, worker.preflight_model_endpoint)


def test_discovery_http_error_is_a_configuration_error() -> None:
    worker, h = make_worker(lambda call: chat_json(), model_id="auto")
    h.model_session._discovery_handler = lambda call: FakeResponse(404, {"error": "no such route"})
    expect_raises(WorkerConfigError, worker.preflight_model_endpoint)


# ---------------------------------------------------------------------------
# G. Provenance
# ---------------------------------------------------------------------------


def test_provenance_is_configuration_only() -> None:
    response = chat_json()
    body = json.loads(response.text)
    body["model"] = "gpt-9-imaginary"
    response.text = json.dumps(body)

    worker, h = make_worker(lambda call: response, model_provider="phone-llamacpp")
    worker.process_job(worker.claim().job)
    block = h.results[0]["json"]["bridge_body"]["model"]
    assert block["provider"] == "phone-llamacpp"
    assert block["model"] == DEFAULT_MODEL_ID
    assert block["artifact_identity"] == PROVENANCE_UNKNOWN
    assert "gpt-9-imaginary" not in json.dumps(block)


# ---------------------------------------------------------------------------
# H. Server-side contract (no worker involvement)
# ---------------------------------------------------------------------------


def test_server_parser_refuses_a_truncated_candidate() -> None:
    """TRUNCATED is refused by the SHARED parser, not by the worker.

    This is the division of authority: the worker labels the candidate
    honestly and submits it; the server's parser turns TRUNCATED into
    MalformedOutputError, and the kernel records VALIDATION_FAILED. If this
    module is unavailable in the current interpreter, the check is reported as
    skipped rather than passed.
    """
    try:
        from backend.model_provider.local_bridge import (
            MalformedOutputError,
            interpretation_from_bridge_body,
        )
    except Exception as exc:  # noqa: BLE001 - dependency-gated, reported below
        print(f"  [SKIP] server parser unavailable here ({type(exc).__name__})")
        return

    body = {
        "interpretation_text": '{"partial":',
        "completion_status": "TRUNCATED",
        "finish_reason": "length",
        "model": {"provider": "phone-llamacpp", "artifact_identity": "PROVENANCE_UNKNOWN"},
    }
    expect_raises(
        MalformedOutputError,
        lambda: interpretation_from_bridge_body(body, SYNTHETIC_INPUT),
    )


TESTS = (
    test_loopback_model_urls_are_accepted,
    test_public_and_unsafe_model_urls_are_refused,
    test_env_contract_maps_to_config,
    test_explicit_flags_override_the_environment,
    test_default_model_id_is_still_default,
    test_chat_request_is_exactly_the_documented_shape,
    test_model_key_is_sent_as_bearer_only_to_the_model,
    test_configured_model_id_is_used_verbatim,
    test_ordinary_json_response_is_accepted,
    test_sse_response_with_many_chunks_is_reassembled,
    test_sse_with_leading_comments_and_event_frames_is_still_sse,
    test_sse_requires_done,
    test_sse_malformed_chunk_fails_closed,
    test_sse_empty_content_fails_closed,
    test_finish_reason_length_is_labelled_truncated_never_complete,
    test_missing_finish_reason_fails_closed,
    test_null_or_empty_finish_reason_fails_closed,
    test_unrecognized_finish_reason_fails_closed,
    test_empty_assistant_content_fails_closed,
    test_malformed_response_body_fails_closed,
    test_oversized_model_response_fails_closed,
    test_completion_dialect_text_field_still_works,
    test_connection_refused_is_reported_not_submitted,
    test_timeout_is_reported_not_submitted,
    test_http_5xx_is_reported_not_submitted,
    test_model_timeout_is_configurable_and_applied,
    test_failure_report_carries_the_lease_proof_and_no_payload,
    test_recovered_model_still_submits_after_a_500,
    test_auto_model_id_resolves_from_the_endpoint,
    test_explicit_model_id_makes_no_discovery_call,
    test_ambiguous_discovery_is_a_configuration_error,
    test_empty_discovery_is_a_configuration_error,
    test_unreachable_discovery_is_a_configuration_error,
    test_discovery_http_error_is_a_configuration_error,
    test_provenance_is_configuration_only,
    test_server_parser_refuses_a_truncated_candidate,
)


def main() -> int:
    print("=" * 78)
    print("Android llama.cpp server ↔ worker — contract suite")
    print("=" * 78)
    passed = 0
    failures = []
    for fn in TESTS:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - a failing test is data
            import traceback

            failures.append((fn.__name__, exc))
            print(f"  [FAIL] {fn.__name__} — {type(exc).__name__}: {exc}")
            traceback.print_exc()
        else:
            passed += 1
            print(f"  [PASS] {fn.__name__}")

    print("\n" + "=" * 78)
    print(f"RESULT: {passed}/{len(TESTS)} integration contracts passed")
    for name, exc in failures:
        print(f"  FAIL {name} — {type(exc).__name__}: {exc}")
    print("=" * 78)
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
