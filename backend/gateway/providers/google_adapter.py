"""Google AI Studio (Gemini) adapter.

Free-tier models (July 2026): gemini-3.5-flash, gemini-3.6-flash.
Legacy gemini-2.0-flash was deprecated on June 1, 2026.
"""
import time
from ..normalized_response import NormalizedResponse
from ..provider_adapter import ProviderAdapter, ProviderCapability


class GoogleAdapter(ProviderAdapter):
    """Google AI Studio via google-genai SDK. Free-tier models: gemini-3.5-flash / gemini-3.6-flash."""

    MODELS = ["gemini-3.5-flash", "gemini-3.6-flash"]  # free-tier, newest first

    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        t0 = time.time()
        try:
            from google import genai
            from google.genai import types as genai_types
        except ImportError:
            return NormalizedResponse(content="", provider="google", model=self.MODELS[0],
                                       error="google-genai SDK not installed", latency_ms=0)

        if not self._api_key:
            return NormalizedResponse(content="", provider="google", model=self.MODELS[0],
                                       error="GOOGLE_API_KEY not configured", latency_ms=0)

        last_error = None
        for model_id in self.MODELS:
            try:
                client = genai.Client(api_key=self._api_key)
                config = genai_types.GenerateContentConfig(
                    system_instruction=system_prompt if system_prompt else None,
                    temperature=temperature if temperature is not None else None,
                    max_output_tokens=max_tokens,
                )
                res = client.models.generate_content(
                    model=model_id,
                    contents=str(prompt),
                    config=config,
                )
                elapsed = (time.time() - t0) * 1000
                if res and res.text:
                    return NormalizedResponse(content=res.text, provider="google", model=model_id,
                                              latency_ms=round(elapsed, 1), finish_reason="stop")
                return NormalizedResponse(content="", provider="google", model=model_id,
                                           error="Empty response", latency_ms=round(elapsed, 1))
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                elapsed = (time.time() - t0) * 1000
                # Deprecated/unavailable model — try fallback model
                if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg or "403" in error_msg or "404" in error_msg:
                    last_error = error_msg
                    continue
                # Other errors are non-recoverable
                return NormalizedResponse(content="", provider="google", model=model_id,
                                           error=error_msg, latency_ms=round(elapsed, 1))

        elapsed = (time.time() - t0) * 1000
        return NormalizedResponse(content="", provider="google", model=self.MODELS[0],
                                   error=last_error or "All Google models failed",
                                   latency_ms=round(elapsed, 1))

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="google", model=self.MODELS[0],
            reasoning_level=2, context_window=1048576,
            expected_latency_ms=1500,
            supports_structured_output=True,
            supports_financial_analysis=True,
            supports_rag=True,
            supports_long_context=True,
        )
