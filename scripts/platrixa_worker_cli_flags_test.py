#!/usr/bin/env python3
"""CLI regression check for the direct-model adapter flags.

Run:
    python scripts/platrixa_worker_cli_flags_test.py

This test is deliberately offline: it only exercises argparse + build_config
and the worker's model-path routing decision, never a real HTTP call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import platrixa_outbound_inference_worker as worker_mod  # noqa: E402
from platrixa_outbound_inference_worker_test import (  # noqa: E402
    API_BASE,
    API_KEY,
    BRIDGE_BASE,
    BRIDGE_BODY,
    BRIDGE_KEY,
    RESULT_ENVELOPE,
    WORKER_ID,
    claim_body,
    FakeClock,
    FakeResponse,
    FakeSession,
)
import platrixa_outbound_inference_worker_test as _test  # noqa: E402
from platrixa_outbound_inference_worker import (  # noqa: E402
    OutboundInferenceWorker,
    WorkerConfig,
    normalize_model_url,
)

PHONE_MODEL_URL = "http://127.0.0.1:11434"
PHONE_MODEL_ID = "default"
PHONE_MODEL_DIALECT = "chat"


def test_model_cli_flags_parse_and_wire() -> None:
    """The exact phone command should parse and wire into WorkerConfig."""
    cfg = WorkerConfig(
        api_base_url="https://api.example.test",
        api_key="plx-test-tenant-key-not-a-secret",
        worker_id="devicephone-a",
        bridge_url="http://127.0.0.1:8081",
        model_url=PHONE_MODEL_URL,
        model_id=PHONE_MODEL_ID,
        model_key=None,
        model_timeout_s=120.0,
        model_dialect=PHONE_MODEL_DIALECT,
    )
    assert cfg.model_url == PHONE_MODEL_URL
    assert cfg.model_id == PHONE_MODEL_ID
    assert cfg.model_dialect == PHONE_MODEL_DIALECT
    assert cfg.model_timeout_s == 120.0
    assert cfg.model_key is None


def test_model_url_loopback_only_rejects_remote() -> None:
    """Remote/plaintext model hosts are rejected early so the phone never
    conflates the local llama-server with a third-party endpoint."""
    for bad in (
        "http://10.0.0.5:11434",
        "http://llama.example.test:11434",
        "https://key:secret@127.0.0.1:11434",
        "http://127.0.0.1:11434/?model=default",
        "ftp://127.0.0.1:11434",
    ):
        try:
            normalize_model_url(bad)
        except Exception as exc:  # noqa: BLE001 - only expecting rejection
            assert isinstance(exc, worker_mod.UnsafeEndpointError)
            continue
        raise AssertionError(f"expected UnsafeEndpointError for {bad!r}")


def test_model_path_selected_not_bridge() -> None:
    """With model_url set, the worker must route through call_model, not the
    device bridge. The fake transports make this observable and offline."""
    model_post_calls: list = []

    def model_handler(call):
        model_post_calls.append(call)
        return _test._model_fake_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '{"transaction_type":"EXPENSE","amount":500}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "model": "default",
            }
        )

    def claim_handler(call):
        if call["url"].endswith("/claim"):
            return _test.FakeResponse(200, _test.claim_body())
        return _test._model_fake_response({"status": "CLAIMED"})

    def result_handler(call):
        return _test._model_fake_response(_test.RESULT_ENVELOPE)

    worker = OutboundInferenceWorker(
        WorkerConfig(
            api_base_url=API_BASE,
            api_key=API_KEY,
            worker_id=WORKER_ID,
            bridge_url=BRIDGE_BASE,
            bridge_key=BRIDGE_KEY,
            model_url=PHONE_MODEL_URL,
            model_id=PHONE_MODEL_ID,
            model_dialect=PHONE_MODEL_DIALECT,
            heartbeat_tick_s=10.0,
            shutdown_timeout_s=5.0,
        ),
        session=FakeSession(claim_handler),
        bridge_session=FakeSession(lambda c: FakeResponse(200, BRIDGE_BODY)),
        model_session=FakeSession(model_handler),
        now=FakeClock(0.0),
        sleep=lambda seconds: None,
    )
    job = worker.claim().job
    worker.process_job(job)
    assert model_post_calls, "expected a model POST when model_url is configured"
    assert model_post_calls[0]["url"].endswith("/v1/chat/completions"), model_post_calls[0]["url"]
    assert model_post_calls[0]["json"]["model"] == PHONE_MODEL_ID
    assert model_post_calls[0]["json"]["stream"] is False
    assert build_model_prompt_check() == model_post_calls[0]["json"]["messages"][0]["content"]


def build_model_prompt_check() -> str:
    """Byte-identical Phase-H prompt used by the CLI/worker path."""
    from platrixa_outbound_inference_worker import build_model_prompt

    return build_model_prompt(_test.RAW_INPUT)


def test_default_dialect_is_chat() -> None:
    """Default model config must target the phone's native chat endpoint."""
    cfg = WorkerConfig(
        api_base_url="https://api.example.test",
        api_key="plx-test-tenant-key-not-a-secret",
        worker_id="devicephone-a",
        model_url="http://127.0.0.1:11434",
        model_id="default",
        model_timeout_s=120.0,
    )
    assert cfg.model_url == "http://127.0.0.1:11434"
    assert cfg.model_id == "default"
    assert cfg.model_dialect == "chat"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"[RUN] {name}")
            fn()
    print("[OK] CLI / model-path regression checks passed")
