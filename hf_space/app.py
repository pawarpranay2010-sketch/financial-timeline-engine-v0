"""
Platrixa — Financial Semantics Specialist Inference (Hugging Face Space)
========================================================================

Serves the EXACT Phase 6C Platrixa specialist artifact — a historical,
FYJC-focused development/evaluation slice of Platrixa's broader
financial-semantic scope (not the definition of the product):

    Base:     Qwen/Qwen2.5-1.5B-Instruct
    Revision: 989aa7980e4cf806f80c7fef2b1adb7bc71aa306
    Adapter:  Pranay-20/platrixa-fyjc-specialist-v0.1
    Revision: b5c0a37cebc00e93144150dbbcaa7b28cadb259e

It is a LANGUAGE-UNDERSTANDING service only. It returns the model's
structured interpretation (18-field ExpandedInterpretation contract). It
NEVER returns journal entries, debit/credit decisions, ledger state, or any
accounting conclusion — the deterministic Platrixa Kernel remains the sole
authority for accounting truth.

FAIL-CLOSED GUARANTEES
----------------------
- If the base model fails to load        -> /health 503, every request 503.
- If the LoRA adapter fails to load      -> the model is DROPPED entirely.
  There is NO base-only fallback, NO other model, NO keyword parsing.
- If generation output is not valid JSON -> explicit malformed-output error.
- If any of the 18 contract fields is missing -> explicit validation error.
- If forbidden accounting fields appear in output -> explicit rejection.

ENDPOINTS
---------
    Gradio named API:  interpret_core
        args: [student_text] -> outputs:
            [ {"interpretation": {...18 fields...}, "model": {...}} |  error
              envelope {"error", "detail", "model"}, runtime metadata str ]
        Client: from gradio_client import Client; Client(space).predict(...)

    The response envelope is byte-compatible with the contract consumed by
    RemoteHFModelProvider (backend/model_provider/remote_hf.py): body key
    "interpretation" + "model" identity metadata.
    (A raw FastAPI POST /interpret is not viable on ZeroGPU — see the
    architecture NOTE near the Gradio section below.)

PROMPT FIDELITY
---------------
Byte-exact Alpaca SFT format proven in Phase 6B/6C and asserted by
scripts/fte_fyjc_57_remote_provider_test.py in the Platrixa repository.
Generation is greedy (temperature 0) exactly like the Phase 6C evaluation.

RUNTIME
-------
ZeroGPU-compatible: model weights load at startup (host/CPU-mapped), and
GPU time is attached per-request inside the @spaces.GPU function. Torch is
NOT pinned here — the ZeroGPU image ships a CUDA-matched torch build; the
actual runtime versions are reported by /health.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Spaces / ZeroGPU decorator (no-op fallback outside HF ZeroGPU hardware)
# ---------------------------------------------------------------------------

try:
    import spaces  # type: ignore[import-not-found] — provided on ZeroGPU Spaces
except Exception:  # local / non-ZeroGPU execution

    class _SpacesShim:
        """No-op stand-in so @spaces.GPU stays unconditional in source."""

        @staticmethod
        def GPU(duration: int = 120):  # noqa: ANN001
            def deco(fn):
                return fn

            return deco

    spaces = _SpacesShim()  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Locked model artifacts (MUST NOT DRIFT — mirrors backend/model_provider/base.py)
# ---------------------------------------------------------------------------

BASE_MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"

ADAPTER_REPO_ID = "Pranay-20/platrixa-fyjc-specialist-v0.1"
ADAPTER_REVISION = "b5c0a37cebc00e93144150dbbcaa7b28cadb259e"

# Phase 6C locked evaluation decoding (temperature=0.0, greedy)
MAX_NEW_TOKENS = 1024
TEMPERATURE = 0.0
TOP_P = 1.0

# The 18-field ExpandedInterpretation contract (names only — semantics are
# verified downstream by the Platrixa schema verifier / grounding gate).
REQUIRED_FIELDS_18: Tuple[str, ...] = (
    "transaction_type",
    "parties",
    "amounts",
    "payment_method",
    "references",
    "ambiguities",
    "grounding",
    "transaction_type_enum",
    "payment_method_enum",
    "ambiguity_flags",
    "referenced_transaction_index",
    "referenced_party",
    "referenced_amount",
    "field_confidences",
    "overall_confidence",
    "suggested_status",
    "safety_flags",
    "scope_flags",
)

FORBIDDEN_ACCOUNTING_FIELDS = frozenset({
    "journal",
    "journal_entry",
    "journal_entries",
    "debit_lines",
    "credit_lines",
    "ledger",
    "balances",
    "trial_balance",
    "debit_account",
    "credit_account",
    "debit",
    "credit",
})

# ---------------------------------------------------------------------------
# Byte-exact Alpaca SFT prompt (asserted against training data in the repo)
# ---------------------------------------------------------------------------

ALPACA_PREFIX = (
    "Below is an instruction that describes a task, paired with an input "
    "that provides further context. Write a response that appropriately "
    "completes the request."
)

ALPACA_INSTRUCTION = (
    "You are the FYJC accounting language-understanding specialist. Parse "
    "the student's accounting language into a grounded structured "
    "interpretation with exactly these 18 fields: transaction_type, parties, "
    "amounts, payment_method, references, ambiguities, grounding, "
    "transaction_type_enum, payment_method_enum, ambiguity_flags, "
    "referenced_transaction_index, referenced_party, referenced_amount, "
    "field_confidences, overall_confidence, suggested_status, "
    "safety_flags, scope_flags. Do not invent missing information. Never "
    "produce journal entries, debit/credit decisions, or accounting "
    "conclusions. Output only machine-readable JSON with exactly these "
    "fields."
)


def build_prompt(text: str) -> str:
    """Byte-exact SFT-format prompt for the student transaction text."""
    return (
        f"{ALPACA_PREFIX}\n\n"
        f"### Instruction:\n{ALPACA_INSTRUCTION}\n\n"
        f"### Input:\n{text}\n\n"
        f"### Response:\n"
    )


# ---------------------------------------------------------------------------
# Model loading state (fail-closed)
# ---------------------------------------------------------------------------

_STATE: Dict[str, Any] = {
    "status": "loading",       # loading | ready | error
    "load_error": "",
    "base_loaded": False,
    "adapter_loaded": False,
}
_MODEL = None
_TOKENIZER = None
_LOAD_LOCK = threading.Lock()


def model_identity() -> Dict[str, Any]:
    """Stable identity metadata returned with every response."""
    identity = {
        "provider": "hf-space",
        "base_model": BASE_MODEL_ID,
        "base_revision": BASE_MODEL_REVISION,
        "adapter_model": ADAPTER_REPO_ID,
        "adapter_revision": ADAPTER_REVISION,
        "adapter_loaded": bool(_STATE["adapter_loaded"]),
        "model_loaded": bool(_STATE["base_loaded"] and _STATE["adapter_loaded"]),
        "status": _STATE["status"],
        "error": _STATE["load_error"],
    }
    # Report the ACTUAL runtime library versions (provenance, not pins).
    try:
        import torch
        identity["torch_version"] = torch.__version__
        identity["cuda_available"] = torch.cuda.is_available()
    except Exception:
        pass
    try:
        import transformers
        identity["transformers_version"] = transformers.__version__
    except Exception:
        pass
    try:
        import peft
        identity["peft_version"] = peft.__version__
    except Exception:
        pass
    try:
        import accelerate
        identity["accelerate_version"] = accelerate.__version__
    except Exception:
        pass
    return identity


def _is_ready() -> bool:
    return _STATE["status"] == "ready" and _MODEL is not None


def _load_model() -> None:
    """Load base + adapter once. Fail-closed on ANY failure."""
    global _MODEL, _TOKENIZER
    with _LOAD_LOCK:
        if _STATE["status"] == "ready":
            return
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer

            _TOKENIZER = AutoTokenizer.from_pretrained(
                BASE_MODEL_ID,
                revision=BASE_MODEL_REVISION,
            )

            # Load on CPU at startup; weights move to the GPU inside the
            # @spaces.GPU call (ZeroGPU has no GPU visible during startup).
            load_kwargs: Dict[str, Any] = {
                "revision": BASE_MODEL_REVISION,
            }
            try:
                import torch
                _MODEL = AutoModelForCausalLM.from_pretrained(
                    BASE_MODEL_ID,
                    torch_dtype=torch.bfloat16,
                    **load_kwargs,
                )
            except TypeError:
                # transformers 5.x renamed torch_dtype -> dtype
                import torch
                _MODEL = AutoModelForCausalLM.from_pretrained(
                    BASE_MODEL_ID,
                    dtype=torch.bfloat16,
                    **load_kwargs,
                )
            _STATE["base_loaded"] = True

            # FAIL-CLOSED adapter attachment. No base-only serving, ever.
            from peft import PeftModel

            _MODEL = PeftModel.from_pretrained(
                _MODEL,
                ADAPTER_REPO_ID,
                revision=ADAPTER_REVISION,
            )
            _STATE["adapter_loaded"] = True
            _MODEL.eval()
            _STATE["status"] = "ready"

        except Exception as exc:  # noqa: BLE001 — fail closed, never exit
            _STATE["load_error"] = (
                f"model/adapter load failed: {type(exc).__name__}: {exc}"
            )
            _MODEL = None
            _TOKENIZER = None
            _STATE["base_loaded"] = False
            _STATE["adapter_loaded"] = False
            _STATE["status"] = "error"


# NOTE: the model is loaded LAZILY on the first inference call, not at
# module import. A ~3 GB download+load at import time stalls startup past
# the ZeroGPU GPU-function detection window ("No @spaces.GPU function
# detected during startup") and keeps /health reporting truthfully until
# the first request completes loading.


# ---------------------------------------------------------------------------
# GPU generation (ZeroGPU: GPU attached only inside this function)
# ---------------------------------------------------------------------------

def _generate_impl(prompt: str) -> str:
    import torch

    _load_model()  # lazy: first call downloads/loads; later calls reuse
    if not _is_ready():
        raise RuntimeError(
            "MODEL_UNAVAILABLE: " + (_STATE["load_error"] or "model not loaded")
        )

    # ZeroGPU: the GPU exists only inside this call — move weights to it.
    device = "cuda" if torch.cuda.is_available() else "cpu"
    _MODEL.to(device)

    inputs = _TOKENIZER(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=2048,
    ).to(device)

    with torch.no_grad():
        outputs = _MODEL.generate(
            **inputs,
            max_new_tokens=MAX_NEW_TOKENS,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            do_sample=False,  # greedy — matches Phase 6C evaluation
            pad_token_id=_TOKENIZER.eos_token_id,
        )

    new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
    return _TOKENIZER.decode(new_tokens, skip_special_tokens=True).strip()


def _gpu_generate(prompt: str) -> str:
    """GPU-bound generation implementation (plain; GPU comes from the
    decorated entry points that call this)."""
    return _generate_impl(prompt)


# ---------------------------------------------------------------------------
# Output parsing / validation (self-contained — mirrors repo logic)
# ---------------------------------------------------------------------------

def extract_json_candidate(raw_response: str) -> Optional[Dict[str, Any]]:
    """Extract a JSON object from raw model output. None if impossible."""
    if not raw_response or not raw_response.strip():
        return None

    text = raw_response.strip()

    fence = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)
    match = fence.search(text)
    if match:
        text = match.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first != -1 and last != -1 and last > first:
        try:
            parsed = json.loads(text[first:last + 1])
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _require_18_fields(candidate: Dict[str, Any]) -> None:
    missing = [f for f in REQUIRED_FIELDS_18 if f not in (candidate or {})]
    if missing:
        raise RuntimeError(
            "MALFORMED_OUTPUT: missing required fields: " + ", ".join(missing)
        )


def _reject_forbidden(candidate: Dict[str, Any]) -> None:
    forbidden = sorted(FORBIDDEN_ACCOUNTING_FIELDS & set(candidate.keys()))
    if forbidden:
        raise RuntimeError(
            "FORBIDDEN_ACCOUNTING_FIELDS: " + ", ".join(forbidden)
        )


def _interpret_impl(text: str) -> Dict[str, Any]:
    """
    Full interpretation pipeline. Returns the exact RemoteHFModelProvider
    envelope: {"interpretation": {...}, "model": {...}}.

    Raises RuntimeError with machine-readable prefixes:
        MODEL_UNAVAILABLE / MALFORMED_OUTPUT / FORBIDDEN_ACCOUNTING_FIELDS
    """
    if not text or not text.strip():
        raise RuntimeError("MALFORMED_OUTPUT: empty input")

    prompt = build_prompt(text.strip())
    raw = _gpu_generate(prompt)

    candidate = extract_json_candidate(raw)
    if candidate is None:
        raise RuntimeError(
            "MALFORMED_OUTPUT: no valid JSON candidate in model output"
        )

    _reject_forbidden(candidate)
    _require_18_fields(candidate)

    return {"interpretation": candidate, "model": model_identity()}


def _error_envelope(message: str) -> Dict[str, Any]:
    kind = message.split(":", 1)[0]
    detail = message.split(":", 1)[-1].strip()
    if kind == "FORBIDDEN_ACCOUNTING_FIELDS":
        return {
            "error": "forbidden_accounting_fields",
            "detail": detail,
            "model": model_identity(),
        }
    if kind == "MALFORMED_OUTPUT":
        return {
            "error": "malformed_output",
            "detail": detail,
            "model": model_identity(),
        }
    return {
        "error": "model_unavailable",
        "detail": detail,
        "model": model_identity(),
    }


def interpret(student_text: str) -> Dict[str, Any]:
    """
    Public entry point. Never raises — failures come back as explicit
    error envelopes (fail-closed), never as fabricated results.
    """
    try:
        return _interpret_impl(student_text)
    except Exception as exc:  # noqa: BLE001 — fail closed with explicit error
        # Normalize non-RuntimeError hardware/framework failures into the
        # same explicit machine-readable taxonomy (never fabricate output).
        message = str(exc)
        if not message.startswith((
            "MODEL_UNAVAILABLE:",
            "MALFORMED_OUTPUT:",
            "FORBIDDEN_ACCOUNTING_FIELDS:",
        )):
            message = f"MODEL_UNAVAILABLE: {type(exc).__name__}: {message}"
        return _error_envelope(message)


# ---------------------------------------------------------------------------
# Gradio UI + named machine API
# ---------------------------------------------------------------------------
# NOTE on architecture: a raw FastAPI POST /interpret mounted via
# gr.mount_gradio_app() was attempted and is NOT viable on ZeroGPU — the
# ZeroGPU supervisor fails startup with "No @spaces.GPU function detected
# during startup" for FastAPI-mounted apps (confirmed on the HF forums and
# by 5 Space rebuilds). The machine-callable API here is therefore the
# Gradio named API `interpret_core` (gradio_client compatible), which
# returns the exact RemoteHFModelProvider envelope as its first output.

import gradio as gr  # noqa: E402


@spaces.GPU(duration=45)  # ZeroGPU: GPU attached for this call's duration
def _ui_interpret(text: str) -> Tuple[Dict[str, Any], str]:
    envelope = interpret(text)
    status_bits = [
        f"status: {_STATE['status']}",
        f"adapter_loaded: {_STATE['adapter_loaded']}",
        f"base: {BASE_MODEL_ID} @ {BASE_MODEL_REVISION[:12]}…",
        f"adapter: {ADAPTER_REPO_ID} @ {ADAPTER_REVISION[:12]}…",
    ]
    if _STATE["load_error"]:
        status_bits.append(f"error: {_STATE['load_error']}")
    return envelope, " | ".join(status_bits)


with gr.Blocks(title="Platrixa — Financial Semantics Specialist") as demo:
    gr.Markdown(
        f"""
# Platrixa — Financial Semantics Specialist

Deterministic financial semantic validation infrastructure for AI-powered
accounting and finance software. This service runs the **exact Phase 6C
artifact** — a historical, FYJC-focused development/evaluation slice of
Platrixa's broader financial-semantic scope. It produces the 18-field
structured interpretation only. **Accounting truth is decided by the
deterministic Platrixa Kernel, not by this model.**

| Artifact | Value |
|---|---|
| Model | `{BASE_MODEL_ID}` |
| Base revision | `{BASE_MODEL_REVISION}` |
| Adapter | `{ADAPTER_REPO_ID}` |
| Adapter revision | `{ADAPTER_REVISION}` |
| Decoding | greedy (temperature=0.0), max_new_tokens={MAX_NEW_TOKENS} |
| Adapter loaded | {"✅ true" if _STATE["adapter_loaded"] else "❌ false"} |
"""
    )

    with gr.Row():
        with gr.Column():
            transaction_input = gr.Textbox(
                label="Transaction text",
                placeholder="e.g. Purchased furniture for cash Rs. 15,000",
                lines=3,
            )
            run_btn = gr.Button("Interpret", variant="primary")
        with gr.Column():
            json_out = gr.JSON(label="Structured interpretation (18 fields)")
            status_out = gr.Textbox(label="Runtime metadata", lines=2)

    run_btn.click(
        _ui_interpret,
        inputs=[transaction_input],
        outputs=[json_out, status_out],
        api_name="interpret_core",  # named machine API (gradio_client)
    )


# ZeroGPU Spaces run app.py directly; the stock template's launch pattern
# (module-level demo.launch()) is the proven startup path on this hardware.
# Gradio binds 7860 (GRADIO_SERVER_PORT) — exactly what the platform proxy
# expects.
demo.launch()
