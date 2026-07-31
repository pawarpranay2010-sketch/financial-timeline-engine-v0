"""Cerebras adapter."""
import time
import os
import requests
from ..normalized_response import NormalizedResponse
from ..provider_adapter import ProviderAdapter, ProviderCapability


class CerebrasAdapter(ProviderAdapter):
    """Cerebras — fast inference. Model configurable via CEREBRAS_MODEL."""

    MODEL = "gpt-oss-120b"

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self.model = os.getenv("CEREBRAS_MODEL", self.MODEL)
        self.endpoint = "https://api.cerebras.ai/v1/chat/completions"
        self.timeout = 30

    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        t0 = time.time()
        if not self._api_key:
            return NormalizedResponse(content="", provider="cerebras", model=self.model,
                                       error="CEREBRAS_API_KEY not configured", latency_ms=0)

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": str(prompt)})

        try:
            payload = {"model": self.model, "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens}
            res = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
            elapsed = (time.time() - t0) * 1000
            if res.status_code == 200:
                data = res.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return NormalizedResponse(
                    content=content, provider="cerebras", model=self.model,
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    latency_ms=round(elapsed, 1),
                    finish_reason=data["choices"][0].get("finish_reason", "stop"),
                )
            return NormalizedResponse(content="", provider="cerebras", model=self.model,
                                       error=f"HTTP {res.status_code}: {res.text[:200]}",
                                       latency_ms=round(elapsed, 1))
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            return NormalizedResponse(content="", provider="cerebras", model=self.model,
                                       error=f"{type(e).__name__}: {e}",
                                       latency_ms=round(elapsed, 1))

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="cerebras", model=self.model,
            reasoning_level=2, context_window=131072,
            expected_latency_ms=500,
            rpm_limit=30,
        )
