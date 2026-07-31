"""RapidAPI adapter — uses configured host/endpoint."""
import time
import os
import requests
from ..normalized_response import NormalizedResponse
from ..provider_adapter import ProviderAdapter, ProviderCapability


class RapidAPIAdapter(ProviderAdapter):
    """RapidAPI — requires RAPIDAPI_HOST and RAPIDAPI_ENDPOINT env vars."""

    def __init__(self, api_key: str = ""):
        super().__init__(api_key)
        self.host = os.getenv("RAPIDAPI_HOST", "open-ai21.p.rapidapi.com")
        self.endpoint_path = os.getenv("RAPIDAPI_ENDPOINT", "/chat/completions")
        self.base_url = f"https://{self.host}{self.endpoint_path}"
        self.timeout = 30

    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        t0 = time.time()
        if not self._api_key:
            return NormalizedResponse(content="", provider="rapidapi", model="gpt-4o-mini",
                                       error="RAPIDAPI_KEY not configured", latency_ms=0)

        headers = {
            "x-rapidapi-key": self._api_key,
            "x-rapidapi-host": self.host,
            "Content-Type": "application/json",
        }
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": str(prompt)})

        try:
            payload = {"model": os.getenv("RAPIDAPI_MODEL", "gpt-4o-mini"),
                       "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            res = requests.post(self.base_url, headers=headers, json=payload, timeout=self.timeout)
            elapsed = (time.time() - t0) * 1000
            if res.status_code == 200:
                data = res.json()
                content = data["choices"][0]["message"]["content"] if "choices" in data else ""
                usage = data.get("usage", {}) if content else {}
                return NormalizedResponse(
                    content=content, provider="rapidapi",
                    model=os.getenv("RAPIDAPI_MODEL", "gpt-4o-mini"),
                    input_tokens=usage.get("prompt_tokens"),
                    output_tokens=usage.get("completion_tokens"),
                    latency_ms=round(elapsed, 1),
                    finish_reason="stop" if content else "",
                )
            return NormalizedResponse(content="", provider="rapidapi", model="",
                                       error=f"HTTP {res.status_code}", latency_ms=round(elapsed, 1))
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            return NormalizedResponse(content="", provider="rapidapi", model="",
                                       error=f"{type(e).__name__}: {e}",
                                       latency_ms=round(elapsed, 1))

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="rapidapi", model=os.getenv("RAPIDAPI_MODEL", "gpt-4o-mini"),
            reasoning_level=2, context_window=128000,
            expected_latency_ms=2000,
            supports_structured_output=True,
            supports_financial_analysis=True,
        )
