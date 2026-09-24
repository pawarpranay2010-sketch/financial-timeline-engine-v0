"""
Platrixa — Modal serverless model inference (Phase 7R)
======================================================

WHY THIS EXISTS
---------------
Render Free (512 MB RAM) cannot load Qwen2.5-1.5B-Instruct: every production
load attempt is killed by the platform after ~75–96 s (Phase 7R audit, 6/6
reproductions). This service hosts the EXACT Phase 6C model artifact on a
Modal serverless GPU container and exposes a minimal HTTP contract to the
existing Platrixa ModelProvider boundary.

Architecture position (nothing downstream changes):

    Student → Cloudflare Pages → Pages Function → Render FastAPI
        → Kernel → ModelProvider → RemoteHFModelProvider
        → THIS SERVICE → Qwen 1.5B + pinned LoRA
        → structured interpretation JSON
        → existing schema validation / grounding / deterministic accounting
        → PostgreSQL

LOCKED MODEL ARTIFACT (MUST NOT DRIFT)
--------------------------------------
    Base:     Qwen/Qwen2.5-1.5B-Instruct
    Revision: 989aa7980e4cf806f80c7fef2b1adb7bc71aa306
    Adapter:  Pranay-20/platrixa-financial-semantic-v0.1
    Revision: b5c0a37cebc00e93144150dbbcaa7b28cadb259e

These are the exact artifacts evaluated in Phase 6C (98% transaction type,
97% parties, 100% amounts, 0% accounting leakage on the untouched 100-example
test set). They are hard-pinned below and passed as `revision=` at load.

PROMPT FIDELITY
---------------
The adapter was SFT-trained on Alpaca-formatted records
(training_data/fyjc_specialist_*_formatted.jsonl, `text` field):

    Below is an instruction that describes a task, paired with an input that
    provides further context. Write a response that appropriately completes
    the request.

    ### Instruction:
    <fixed instruction naming the 18 fields>

    ### Input:
    <student transaction text>

    ### Response:
    <ground-truth 18-field JSON>

This service replicates that template byte-for-byte and feeds the RAW
completion string to the tokenizer (no chat template — the adapter never
saw one). build_prompt() is asserted against the formatted training data
by scripts/fte_fyjc_57_remote_provider_test.py.

SAFETY BOUNDARIES
-----------------
- This service is LANGUAGE UNDERSTANDING ONLY. It returns the model's
  structured interpretation. It never returns journal entries, debit/credit
  lines, ledger state, balances, or any accounting conclusion.
- Adapter loading is FAIL-CLOSED: if the adapter cannot be loaded, the model
  is dropped and /health reports unhealthy. There is NO base-only fallback.
- Generation is deterministic (temperature 0, greedy).

RUN
---
    modal deploy training/modal_inference.py

Requires Modal authentication (modal token set) — Phase 6B/6C training ran
from Colab, so the first deploy must be performed where Modal is authed.
The public URL is printed by `modal deploy`; put it (without trailing slash)
into the Render env var PLATRIXA_MODEL_ENDPOINT_URL.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

import modal

# ---------------------------------------------------------------------------
# Locked model artifacts (mirrors backend/model_provider/base.py pins)
# ---------------------------------------------------------------------------

BASE_MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
BASE_MODEL_REVISION = "989aa7980e4cf806f80c7fef2b1adb7bc71aa306"

ADAPTER_REPO_ID = "Pranay-20/platrixa-financial-semantic-v0.1"
ADAPTER_REVISION = "b5c0a37cebc00e93144150dbbcaa7b28cadb259e"

# ---------------------------------------------------------------------------
# Inference runtime configuration
# ---------------------------------------------------------------------------

# Exact dependency versions proven during Phase 6B/6C (Modal training/eval).
# Nothing beyond these + FastAPI/uvicorn (ASGI) + hf_transfer (fast download)
# is installed. Do not add packages casually: this is an inference-only image.
INFERENCE_PACKAGES = [
    "torch==2.11.0",
    "transformers==5.16.1",
    "peft==0.20.0",
    "accelerate==1.14.0",
    "safetensors>=0.4",
    "huggingface_hub>=0.30",
    "hf_transfer>=0.1",
    "fastapi>=0.115",
    "uvicorn>=0.30",
]

# T4 (16 GB) is the cheapest GPU class that comfortably holds the 1.5B model
# in fp16/bf16 (~3.5 GB weights) with KV cache and PEFT overhead. The adapter
# itself adds only tens of MB.
_GPU = os.environ.get("PLATRIXA_MODAL_GPU", "T4")

# Generation parameters mirror the LocalHF provider defaults.
MAX_NEW_TOKENS = 512
TEMPERATURE = 0.0
TOP_P = 1.0

# Weights are cached on a Modal Volume so cold containers do not re-download
# the ~3 GB base model from Hugging Face on every scale-up.
HF_CACHE_VOL = modal.Volume.from_name("platrixa-hf-cache", create_if_missing=True)

app = modal.App("platrixa-model-inference")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(*INFERENCE_PACKAGES)
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)


# ---------------------------------------------------------------------------
# Prompt construction (byte-exact Alpaca SFT format)
# ---------------------------------------------------------------------------

ALPACA_PREFIX = (
    "Below is an instruction that describes a task, paired with an input "
    "that provides further context. Write a response that appropriately "
    "completes the request."
)

# Byte-exact instruction used in training_data/*_formatted.jsonl.
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
    """Build the raw completion prompt in the exact SFT format."""
    return (
        f"{ALPACA_PREFIX}\n\n"
        f"### Instruction:\n{ALPACA_INSTRUCTION}\n\n"
        f"### Input:\n{text}\n\n"
        f"### Response:\n"
    )


# ---------------------------------------------------------------------------
# JSON extraction (self-contained; container has no backend/ package)
# ---------------------------------------------------------------------------

_FORBIDDEN_ACCOUNTING_FIELDS = frozenset({
    "journal",
    "journal_entry",
    "debit_lines",
    "credit_lines",
    "ledger",
    "balances",
    "debit_account",
    "credit_account",
})


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


# ---------------------------------------------------------------------------
# Model container
# ---------------------------------------------------------------------------

@app.cls(
    image=image,
    gpu=_GPU,
    volumes={"/root/.cache/huggingface": HF_CACHE_VOL},
    timeout=300,
    startup_timeout=900,   # first-ever start downloads ~3 GB into the volume
    scaledown_window=600,  # keep a warm container for 10 min between requests
)
class PlatrixaModelInference:
    """Stateful GPU container holding the pinned Qwen + LoRA artifact."""

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_error: str = ""
        self._adapter_loaded: bool = False
        self._model_loaded: bool = False

    # -- lifecycle ----------------------------------------------------------

    @modal.enter()
    def load(self) -> None:
        """Load base + adapter once per warm container. Fail-closed."""
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                BASE_MODEL_ID,
                revision=BASE_MODEL_REVISION,
            )

            # transformers 5.x may accept `dtype` instead of `torch_dtype`;
            # try the legacy kwarg first, then the new one.
            load_kwargs: Dict[str, Any] = {
                "revision": BASE_MODEL_REVISION,
                "device_map": "auto",
            }
            try:
                self._model = AutoModelForCausalLM.from_pretrained(
                    BASE_MODEL_ID,
                    torch_dtype=torch.bfloat16,
                    **load_kwargs,
                )
            except TypeError:
                self._model = AutoModelForCausalLM.from_pretrained(
                    BASE_MODEL_ID,
                    dtype=torch.bfloat16,
                    **load_kwargs,
                )

            # Attach the pinned Platrixa LoRA adapter.
            #
            # FAIL-CLOSED: an adapter that fails to load means NO model at
            # all. We never serve base-only inference from this service — a
            # base-only model would produce silently wrong FYJC
            # interpretations (Phase 6C: base scores far below the adapter).
            from peft import PeftModel

            try:
                self._model = PeftModel.from_pretrained(
                    self._model,
                    ADAPTER_REPO_ID,
                    revision=ADAPTER_REVISION,
                )
                self._adapter_loaded = True
            except Exception as exc:  # noqa: BLE001 — fail closed
                self._load_error = (
                    f"LoRA adapter loading failed for '{ADAPTER_REPO_ID}' "
                    f"revision '{ADAPTER_REVISION}': {exc}"
                )
                self._model = None
                self._tokenizer = None
                self._model_loaded = False
                return

            self._model.eval()
            self._model_loaded = True

        except Exception as exc:  # noqa: BLE001 — fail closed
            self._load_error = f"Model loading failed: {exc}"
            self._model = None
            self._tokenizer = None
            self._model_loaded = False
            self._adapter_loaded = False

    # -- identity -----------------------------------------------------------

    def identity(self) -> Dict[str, Any]:
        return {
            "base_model_id": BASE_MODEL_ID,
            "base_revision": BASE_MODEL_REVISION,
            "adapter_repo_id": ADAPTER_REPO_ID,
            "adapter_revision": ADAPTER_REVISION,
            "model_loaded": self._model_loaded,
            "adapter_loaded": self._adapter_loaded,
            "error": self._load_error,
        }

    def _is_ready(self) -> bool:
        return self._model_loaded and self._adapter_loaded

    # -- inference ----------------------------------------------------------

    def interpret(self, text: str) -> Dict[str, Any]:
        """
        One deterministic interpretation pass (in-process; the ASGI layer
        calls this directly on the warm container).

        Returns {"interpretation": <18-field dict>, "model": <identity>} on
        success. Raises RuntimeError with a machine-readable prefix on
        failure kinds so the ASGI layer can map them to HTTP statuses.
        """
        if not self._is_ready():
            raise RuntimeError(
                "MODEL_UNAVAILABLE: " + (self._load_error or "model not loaded")
            )
        if not text or not text.strip():
            raise RuntimeError("MALFORMED_OUTPUT: empty input")

        import torch

        prompt = build_prompt(text.strip())

        inputs = self._tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=2048,
        )
        device = next(self._model.parameters()).device
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=MAX_NEW_TOKENS,
                temperature=TEMPERATURE,
                top_p=TOP_P,
                do_sample=TEMPERATURE > 0,  # greedy at temperature 0
                pad_token_id=self._tokenizer.eos_token_id,
            )

        new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
        generated = self._tokenizer.decode(
            new_tokens,
            skip_special_tokens=True,
        ).strip()

        candidate = extract_json_candidate(generated)
        if candidate is None:
            raise RuntimeError("MALFORMED_OUTPUT: no valid JSON candidate in model output")

        forbidden = sorted(_FORBIDDEN_ACCOUNTING_FIELDS & set(candidate.keys()))
        if forbidden:
            raise RuntimeError(
                "FORBIDDEN_ACCOUNTING_FIELDS: " + ", ".join(forbidden)
            )

        return {"interpretation": candidate, "model": self.identity()}

    # -- ASGI HTTP surface ----------------------------------------------------

    @modal.asgi_app(label="platrixa-model-inference")
    def api(self):
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import JSONResponse

        web_app = FastAPI(title="Platrixa Model Inference")

        @web_app.get("/health")
        async def health():
            ident = self.identity()
            if self._is_ready():
                return JSONResponse(
                    status_code=200,
                    content={"status": "healthy", **ident},
                )
            # Do NOT report healthy when model/adapter failed to load.
            return JSONResponse(
                status_code=503,
                content={"status": "unhealthy", **ident},
            )

        @web_app.post("/interpret")
        async def interpret(request: Request):
            try:
                payload = await request.json()
            except Exception:
                raise HTTPException(status_code=422, detail="invalid JSON body")
            text = payload.get("text") if isinstance(payload, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise HTTPException(status_code=422, detail="missing 'text'")

            try:
                result = self.interpret(text)
            except RuntimeError as exc:
                message = str(exc)
                if message.startswith("FORBIDDEN_ACCOUNTING_FIELDS:"):
                    return JSONResponse(
                        status_code=422,
                        content={
                            "error": "forbidden_accounting_fields",
                            "detail": message.split(":", 1)[1].strip(),
                        },
                    )
                if message.startswith("MALFORMED_OUTPUT:"):
                    return JSONResponse(
                        status_code=422,
                        content={
                            "error": "malformed_output",
                            "detail": message.split(":", 1)[1].strip(),
                        },
                    )
                # MODEL_UNAVAILABLE and anything else → infrastructure error
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "model_unavailable",
                        "detail": message.split(":", 1)[-1].strip(),
                    },
                )

            return JSONResponse(status_code=200, content=result)

        return web_app
