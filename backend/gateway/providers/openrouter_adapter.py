"""OpenRouter adapter.

Free-tier models rotate frequently. The openrouter/free auto-router
handles model rotation automatically (verified working July 2026).
"""
import time
import requests
from ..normalized_response import NormalizedResponse
from ..provider_adapter import ProviderAdapter, ProviderCapability


class OpenRouterAdapter(ProviderAdapter):
    """OpenRouter — auto-router + specific model fallback.

    PRIMARY: openrouter/free (auto-router that handles model rotation)
    FALLBACK: meta-llama/llama-3.3-70b-instruct:free (specific free model)
    """

    # Auto-router handles model rotation automatically; specific models
    # may rotate in/out of the free tier without notice.
    PRIMARY = "openrouter/free"
    FALLBACK = "meta-llama/llama-3.3-70b-instruct:free"

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self.endpoint = "https://openrouter.ai/api/v1/chat/completions"
        self.timeout = 45

    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        t0 = time.time()
        if not self._api_key:
            return NormalizedResponse(content="", provider="openrouter", model="",
                                       error="OPENROUTER_API_KEY not configured", latency_ms=0)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://streamlit.app",
            "X-Title": "Financial Timeline Engine",
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": str(prompt)})

        for model_id in (self.PRIMARY, self.FALLBACK):
            try:
                payload = {"model": model_id, "messages": messages,
                           "temperature": temperature, "max_tokens": max_tokens}
                res = requests.post(self.endpoint, headers=headers, json=payload, timeout=self.timeout)
                elapsed = (time.time() - t0) * 1000
                if res.status_code == 200:
                    data = res.json()
                    if "choices" in data and data["choices"]:
                        content = data["choices"][0]["message"]["content"]
                        usage = data.get("usage", {})
                        return NormalizedResponse(
                            content=content, provider="openrouter", model=model_id,
                            input_tokens=usage.get("prompt_tokens"),
                            output_tokens=usage.get("completion_tokens"),
                            latency_ms=round(elapsed, 1),
                            finish_reason=data["choices"][0].get("finish_reason", "stop"),
                        )
                elif res.status_code == 429:
                    continue
            except requests.exceptions.Timeout:
                continue
            except Exception:
                continue

        elapsed = (time.time() - t0) * 1000
        return NormalizedResponse(content="", provider="openrouter", model="",
                                   error="All OpenRouter models failed",
                                   latency_ms=round(elapsed, 1))

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="openrouter", model=self.PRIMARY,
            reasoning_level=2, context_window=64000,
            expected_latency_ms=3000,
            supports_structured_output=True,
            supports_financial_analysis=True,
        )
