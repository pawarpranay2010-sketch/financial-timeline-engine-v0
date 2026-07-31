"""Cohere adapter."""
import time
import os
import requests
from ..normalized_response import NormalizedResponse
from ..provider_adapter import ProviderAdapter, ProviderCapability


class CohereAdapter(ProviderAdapter):
    """Cohere — model configurable via COHERE_MODEL."""

    MODEL = "command-r-plus"

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self.model = os.getenv("COHERE_MODEL", self.MODEL)
        self.endpoint = "https://api.cohere.ai/v1/chat"
        self.timeout = 30

    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        t0 = time.time()
        if not self._api_key:
            return NormalizedResponse(content="", provider="cohere", model=self.model,
                                       error="COHERE_API_KEY not configured", latency_ms=0)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "accept": "application/json",
        }

        try:
            payload = {
                "model": self.model,
                "message": str(prompt),
                "preamble": system_prompt if system_prompt else None,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if system_prompt:
                payload["preamble"] = system_prompt
            res = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
            elapsed = (time.time() - t0) * 1000
            if res.status_code == 200:
                data = res.json()
                content = data.get("text", "")
                usage = data.get("meta", {}).get("billed_units", {})
                return NormalizedResponse(
                    content=content, provider="cohere", model=self.model,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    latency_ms=round(elapsed, 1),
                    finish_reason=data.get("finish_reason", "complete"),
                )
            return NormalizedResponse(content="", provider="cohere", model=self.model,
                                       error=f"HTTP {res.status_code}: {res.text[:200]}",
                                       latency_ms=round(elapsed, 1))
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            return NormalizedResponse(content="", provider="cohere", model=self.model,
                                       error=f"{type(e).__name__}: {e}",
                                       latency_ms=round(elapsed, 1))

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="cohere", model=self.model,
            reasoning_level=2, context_window=128000,
            expected_latency_ms=1500,
            supports_rag=True,
            supports_financial_analysis=True,
            supports_long_context=True,
        )
