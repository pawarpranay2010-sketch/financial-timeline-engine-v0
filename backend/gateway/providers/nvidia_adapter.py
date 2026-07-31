"""NVIDIA Nemotron adapter."""
import time
import requests
from ..normalized_response import NormalizedResponse
from ..provider_adapter import ProviderAdapter, ProviderCapability


class NvidiaAdapter(ProviderAdapter):
    """NVIDIA API — model: nvidia/nemotron-3-ultra-550b-a55b."""

    MODEL = "nvidia/nemotron-3-ultra-550b-a55b"

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self.endpoint = "https://api.nvcf.nvidia.com/v2/nvcf/pexec/functions"
        self.timeout = 60

    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        t0 = time.time()
        if not self._api_key:
            return NormalizedResponse(content="", provider="nvidia", model=self.MODEL,
                                       error="NVIDIA_API_KEY not configured", latency_ms=0)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": str(prompt)})

        try:
            payload = {"model": self.MODEL, "messages": messages,
                       "temperature": temperature, "max_tokens": max_tokens}
            res = requests.post(self.endpoint.replace("/functions", "/chat/completions"),
                                headers=headers, json=payload, timeout=self.timeout)
            elapsed = (time.time() - t0) * 1000
            if res.status_code == 200:
                data = res.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return NormalizedResponse(
                    content=content, provider="nvidia", model=self.MODEL,
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    latency_ms=round(elapsed, 1),
                    finish_reason=data["choices"][0].get("finish_reason", "stop"),
                )
            error_body = res.text[:200]
            return NormalizedResponse(content="", provider="nvidia", model=self.MODEL,
                                       error=f"HTTP {res.status_code}: {error_body}",
                                       latency_ms=round(elapsed, 1))
        except requests.exceptions.Timeout:
            elapsed = (time.time() - t0) * 1000
            return NormalizedResponse(content="", provider="nvidia", model=self.MODEL,
                                       error="Timeout", latency_ms=round(elapsed, 1))
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            return NormalizedResponse(content="", provider="nvidia", model=self.MODEL,
                                       error=f"{type(e).__name__}: {e}",
                                       latency_ms=round(elapsed, 1))

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="nvidia", model=self.MODEL,
            reasoning_level=3, context_window=128000,
            expected_latency_ms=5000,
            supports_structured_output=True,
            supports_financial_analysis=True,
            supports_long_context=True,
        )
