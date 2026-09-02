"""
Platrixa — Local Model Runner (FYJC Specialist Inference)
==========================================================

Lazy-loading Hugging Face model runner for local inference.

Supports:
  - Any causal LM (Qwen2.5, Llama, Mistral, etc.)
  - LoRA adapters via PEFT
  - CPU and CUDA execution
  - Configurable via environment variables
  - Lazy loading (model loaded on first inference call)

Environment variables:
  PLATRIXA_FYJC_MODEL_ID   — Hugging Face model ID or local path
  PLATRIXA_FYJC_ADAPTER    — optional LoRA adapter path
  PLATRIXA_FYJC_DEVICE     — "auto", "cpu", "cuda", "mps"
  PLATRIXA_FYJC_DTYPE      — "auto", "float16", "bfloat16", "float32"
  PLATRIXA_FYJC_MAX_TOKENS — max new tokens (default 1024)
  PLATRIXA_FYJC_TEMPERATURE — generation temperature (default 0.1)

Architecture:
    FYJCLLMSpecialist
        ↓
    LocalModelRunner.get_instance()
        ↓
    AutoTokenizer.from_pretrained(model_id)
    AutoModelForCausalLM.from_pretrained(model_id, ...)
    optionally PeftModel.from_pretrained(base_model, adapter_path)
        ↓
    tokenizer(prompt) → model.generate() → decode → raw text
        ↓
    JSON extraction + validation
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MODEL_ID = "Qwen/Qwen2.5-1.5B-Instruct"
DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.1
DEFAULT_TOP_P = 0.95


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def get_model_config() -> Dict[str, Any]:
    """Read model configuration from environment variables."""
    return {
        "model_id": _env("PLATRIXA_FYJC_MODEL_ID", DEFAULT_MODEL_ID),
        "adapter_path": _env("PLATRIXA_FYJC_ADAPTER", ""),
        "device": _env("PLATRIXA_FYJC_DEVICE", "auto"),
        "dtype": _env("PLATRIXA_FYJC_DTYPE", "auto"),
        "max_new_tokens": int(_env("PLATRIXA_FYJC_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
        "temperature": float(_env("PLATRIXA_FYJC_TEMPERATURE", str(DEFAULT_TEMPERATURE))),
        "top_p": float(_env("PLATRIXA_FYJC_TOP_P", str(DEFAULT_TOP_P))),
    }


def check_transformers_available() -> bool:
    """Check if Hugging Face Transformers is installed."""
    try:
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def check_peft_available() -> bool:
    """Check if PEFT (for LoRA adapters) is installed."""
    try:
        import peft  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# LocalModelRunner — singleton with lazy loading
# ---------------------------------------------------------------------------

class LocalModelRunner:
    """Lazy-loading local Hugging Face model runner.

    The model is loaded on first call to `generate()`, not at import time.
    Thread-safe singleton via class-level lock.

    Usage:
        runner = LocalModelRunner()
        text = runner.generate("System: ...\nUser: Purchased furniture from Amit")
        # text is raw model output (hopefully JSON)

        # Check availability without loading:
        if not runner.is_available():
            print(runner.status())
    """

    _instance: Optional["LocalModelRunner"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "LocalModelRunner":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._load_error: Optional[str] = None
        self._config = get_model_config()

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._lock:
            cls._instance = None

    @property
    def model_id(self) -> str:
        return self._config["model_id"]

    @property
    def device(self) -> str:
        return self._config["device"]

    def is_available(self) -> bool:
        """Check if model is loaded and ready. Does NOT trigger loading."""
        return self._loaded and self._model is not None

    def status(self) -> Dict[str, Any]:
        """Return model status without triggering loading."""
        return {
            "model_id": self._config["model_id"],
            "adapter": self._config.get("adapter_path", ""),
            "loaded": self._loaded,
            "available": self.is_available(),
            "error": self._load_error,
            "device": self._config["device"],
            "transformers_installed": check_transformers_available(),
            "peft_installed": check_peft_available(),
        }

    def _resolve_device(self, requested: str) -> str:
        """Resolve device string to actual torch device."""
        try:
            import torch
            if requested == "auto":
                if torch.cuda.is_available():
                    return "cuda"
                elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                    return "mps"
                return "cpu"
            return requested
        except ImportError:
            return "cpu"

    def _resolve_dtype(self, dtype_str: str):
        """Resolve dtype string to torch dtype."""
        try:
            import torch
            if dtype_str == "auto" or dtype_str == "":
                return None  # auto-detect
            mapping = {
                "float16": torch.float16,
                "bfloat16": torch.bfloat16,
                "float32": torch.float32,
            }
            return mapping.get(dtype_str)
        except ImportError:
            return None

    def _load_model(self) -> None:
        """Load model and tokenizer. Called once on first generate()."""
        if self._loaded:
            return

        model_id = self._config["model_id"]
        adapter_path = self._config.get("adapter_path", "")
        device_str = self._config["device"]
        dtype_str = self._config["dtype"]

        # Check transformers
        if not check_transformers_available():
            self._load_error = (
                "Hugging Face Transformers not installed. "
                "Install with: pip install transformers"
            )
            logger.error(self._load_error)
            return

        try:
            import torch
            from transformers import AutoTokenizer, AutoModelForCausalLM

            device = self._resolve_device(device_str)
            dtype = self._resolve_dtype(dtype_str)

            logger.info(f"Loading tokenizer from {model_id}...")
            self._tokenizer = AutoTokenizer.from_pretrained(
                model_id,
                trust_remote_code=True,
            )

            logger.info(f"Loading model from {model_id} (device={device}, dtype={dtype})...")
            load_kwargs: Dict[str, Any] = {
                "trust_remote_code": True,
            }
            if dtype is not None:
                load_kwargs["torch_dtype"] = dtype
            if device == "cpu":
                load_kwargs["device_map"] = None
            else:
                load_kwargs["device_map"] = "auto"

            self._model = AutoModelForCausalLM.from_pretrained(
                model_id,
                **load_kwargs,
            )

            # Load LoRA adapter if configured
            if adapter_path and os.path.isdir(adapter_path):
                if not check_peft_available():
                    self._load_error = (
                        f"PEFT not installed but adapter path configured: {adapter_path}. "
                        "Install with: pip install peft"
                    )
                    logger.error(self._load_error)
                    return

                from peft import PeftModel
                logger.info(f"Loading LoRA adapter from {adapter_path}...")
                self._model = PeftModel.from_pretrained(
                    self._model,
                    adapter_path,
                )
                logger.info("LoRA adapter loaded successfully.")

            # Move to device if needed
            if device == "cpu" and hasattr(self._model, "to"):
                self._model = self._model.to("cpu")

            self._model.eval()
            self._loaded = True
            self._load_error = None
            logger.info(f"Model loaded successfully: {model_id}")

        except Exception as e:
            self._load_error = f"Model loading failed: {e}"
            logger.error(self._load_error)
            self._model = None
            self._tokenizer = None

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_new_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
    ) -> Tuple[Optional[str], str]:
        """Generate text from the local model.

        Args:
            prompt: User message text.
            system_prompt: System instruction (if empty, uses default).
            max_new_tokens: Override max tokens.
            temperature: Override temperature.
            top_p: Override top_p.

        Returns:
            (generated_text, error_message)
            On success: (text, "")
            On failure: (None, error_message)
        """
        if not self._loaded:
            self._load_model()

        if not self._loaded or self._model is None or self._tokenizer is None:
            error = self._load_error or "Model not loaded"
            return None, error

        try:
            import torch

            max_tok = max_new_tokens or self._config["max_new_tokens"]
            temp = temperature if temperature is not None else self._config["temperature"]
            tp = top_p if top_p is not None else self._config["top_p"]

            # Build chat-style prompt
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            # Use chat template if available
            if hasattr(self._tokenizer, "apply_chat_template"):
                input_text = self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            else:
                # Fallback: manual prompt formatting
                parts = []
                if system_prompt:
                    parts.append(f"System: {system_prompt}")
                parts.append(f"User: {prompt}")
                parts.append("Assistant:")
                input_text = "\n".join(parts)

            inputs = self._tokenizer(
                input_text,
                return_tensors="pt",
                truncation=True,
                max_length=2048,
            )

            # Move inputs to same device as model
            device = next(self._model.parameters()).device
            inputs = {k: v.to(device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs,
                    max_new_tokens=max_tok,
                    temperature=temp,
                    top_p=tp,
                    do_sample=temp > 0,
                    pad_token_id=self._tokenizer.eos_token_id,
                )

            # Decode only the new tokens
            new_tokens = outputs[0][inputs["input_ids"].shape[1]:]
            generated = self._tokenizer.decode(
                new_tokens,
                skip_special_tokens=True,
            )

            return generated.strip(), ""

        except Exception as e:
            return None, f"Generation failed: {e}"

    def unload(self) -> None:
        """Unload model to free memory."""
        try:
            import torch
            if self._model is not None:
                del self._model
            if self._tokenizer is not None:
                del self._tokenizer
            self._model = None
            self._tokenizer = None
            self._loaded = False
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            self._model = None
            self._tokenizer = None
            self._loaded = False


# ---------------------------------------------------------------------------
# Mock model runner for testing
# ---------------------------------------------------------------------------

class MockModelRunner:
    """Deterministic mock model runner for testing without a real model.

    Returns pre-defined JSON responses for known inputs.
    Used in tests where no local model artifact is available.
    """

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        self._responses = responses or {}
        self._loaded = True

    def is_available(self) -> bool:
        return True

    def status(self) -> Dict[str, Any]:
        return {"model_id": "mock", "loaded": True, "available": True}

    def generate(
        self,
        prompt: str,
        system_prompt: str = "",
        max_new_tokens: int = 1024,
        temperature: float = 0.1,
        top_p: float = 0.95,
    ) -> Tuple[Optional[str], str]:
        """Return mock response for known inputs."""
        prompt_lower = prompt.lower().strip()

        # Check exact matches first
        for key, response in self._responses.items():
            if key.lower() in prompt_lower:
                return response, ""

        # Default: return a generic valid response
        return json.dumps({
            "transaction_type": "PURCHASE",
            "parties": [],
            "amounts": [],
            "payment_method": "UNKNOWN",
            "references": [],
            "ambiguities": ["could not determine from input"],
            "grounding": {"all_fields_explicitly_grounded": False, "inferred_fields": []},
            "transaction_type_enum": "UNKNOWN",
            "payment_method_enum": "UNKNOWN",
            "ambiguity_flags": ["MISSING_PARTY", "MISSING_AMOUNT", "MISSING_PAYMENT_MODE"],
            "referenced_transaction_index": None,
            "referenced_party": None,
            "referenced_amount": None,
            "field_confidences": [],
            "overall_confidence": "0.20",
            "suggested_status": "REVIEW_REQUIRED",
            "safety_flags": ["LOW_CONFIDENCE"],
            "scope_flags": ["SINGLE_TRANSACTION"],
        }), ""

    def unload(self) -> None:
        pass
