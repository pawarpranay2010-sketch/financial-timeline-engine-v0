"""Groq API adapter."""
import time
import requests
from ..normalized_response import NormalizedResponse
from ..provider_adapter import ProviderAdapter, ProviderCapability


class GroqAdapter(ProviderAdapter):
    """Groq via OpenAI-compatible endpoint."""

    MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "mixtral-8x7b-32768"]

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self.endpoint = "https://api.groq.com/openai/v1/chat/completions"
        self.timeout = 30

    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        t0 = time.time()
        if not self._api_key:
            return NormalizedResponse(content="", provider="groq", model="",
                                       error="GROQ_API_KEY not configured", latency_ms=0)

        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": str(prompt)})

        last_error = None
        for model_id in self.MODELS:
            try:
                payload = {"model": model_id, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens}
                res = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
                elapsed = (time.time() - t0) * 1000
                if res.status_code == 200:
                    data = res.json()
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    return NormalizedResponse(
                        content=content, provider="groq", model=model_id,
                        input_tokens=usage.get("prompt_tokens"),
                        output_tokens=usage.get("completion_tokens"),
                        latency_ms=round(elapsed, 1),
                        finish_reason=data["choices"][0].get("finish_reason", "stop"),
                    )
                elif res.status_code == 429:
                    last_error = f"Groq {model_id} rate-limited (429)"
                else:
                    last_error = f"Groq {model_id} HTTP {res.status_code}"
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"

        elapsed = (time.time() - t0) * 1000
        return NormalizedResponse(content="", provider="groq", model="",
                                   error=last_error or "All Groq models failed",
                                   latency_ms=round(elapsed, 1))

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="groq", model=self.MODELS[0],
            reasoning_level=1, context_window=32768,
            expected_latency_ms=800,
            supports_financial_analysis=True,
            rpm_limit=30,
        )
