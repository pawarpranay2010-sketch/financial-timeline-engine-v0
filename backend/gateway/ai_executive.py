"""AI Executive — orchestrates the complete provider chain.

Request → AdmissionController → Router → Provider execution → Normalization → Fallback
"""
import time
import logging
from typing import Optional, List
from .normalized_response import NormalizedResponse
from .provider_manager import ProviderManager
from .router import Router, RouteDecision, TASK_FINANCIAL_ANALYSIS, TASK_SIMPLE, TASK_RAG, TASK_FALLBACK
from .admission_controller import AdmissionController, AdmissionResult
from .redis_quota import RedisQuotaTracker
from .capability_registry import CapabilityRegistry, ProviderMetadata

logger = logging.getLogger(__name__)


class AIExecutive:
    """Central orchestrator for all AI provider calls.

    Integrates:
    - AdmissionController (context fit, rate limits)
    - Router (workload-aware provider selection)
    - ProviderManager (adapter lifecycle)
    - RedisQuotaTracker (cross-worker state)
    - Fallback chain (automatically try next eligible provider on failure)
    """

    def __init__(self):
        self.provider_manager = ProviderManager()
        self.registry = CapabilityRegistry()
        self.router = Router(self.registry)
        self.admission = AdmissionController()
        self.quota = RedisQuotaTracker()
        self._populate_registry()

    def _populate_registry(self) -> None:
        """Register all healthy providers in the capability registry."""
        for name in self.provider_manager.DEFAULT_PRIORITY:
            adapter = self.provider_manager.get(name)
            if adapter and adapter.health_check():
                cap = adapter.capability()
                meta = ProviderMetadata(
                    provider=name,
                    model=cap.model,
                    capabilities=cap,
                    priority=self.provider_manager.DEFAULT_PRIORITY.index(name),
                    model_id=cap.model,
                )
                self.registry.register(meta)

    def generate(self, prompt: str, system_prompt: str = "",
                 temperature: float = 0.3, max_tokens: int = 4096,
                 task_type: str = TASK_FINANCIAL_ANALYSIS) -> NormalizedResponse:
        """Execute an AI request through the full gateway pipeline.

        Steps:
        1. Route decision (which provider)
        2. Admission check (context fit, quota)
        3. Provider execution
        4. Normalization
        5. Fallback if needed
        """
        # 1. Route
        input_tokens = self.admission.estimate_tokens(prompt) + self.admission.estimate_tokens(system_prompt)
        route = self.router.route(task_type, input_tokens, max_tokens)
        if not route:
            return NormalizedResponse(
                content="", provider="", model="",
                error="No eligible provider found. Check API key configuration.",
            )

        # Check if the provider has a configured key
        adapter = self.provider_manager.get(route.provider)
        if not adapter or not adapter.health_check():
            logger.warning(f"Provider '{route.provider}' not available, trying fallback")
            return self._fallback(prompt, system_prompt, temperature, max_tokens, task_type,
                                   [route.provider])

        cap = route.capability.capabilities

        # 2. Admission
        result = self.admission.admit(
            prompt, system_prompt,
            context_window=cap.context_window,
            output_tokens=max_tokens,
            rpm_limit=cap.rpm_limit,
            current_rpm=self.quota.get_rpm(route.provider),
        )

        if not result.allowed:
            if not result.context_ok:
                # Oversized context — try long-context route
                long_route = self.router.route("long_context", input_tokens, max_tokens)
                if long_route and long_route.provider != route.provider:
                    return self._execute_provider(
                        long_route.provider, prompt, system_prompt, temperature, max_tokens,
                        [route.provider],
                    )
            if not result.quota_ok:
                # Rate limited — try fallback
                return self._fallback(prompt, system_prompt, temperature, max_tokens, task_type,
                                       [route.provider])

        # 3. Execute with fallback
        return self._execute_provider(
            route.provider, prompt, system_prompt, temperature, max_tokens, []
        )

    def _execute_provider(self, provider_name: str, prompt: str,
                          system_prompt: str, temperature: float,
                          max_tokens: int,
                          attempted: List[str]) -> NormalizedResponse:
        """Execute with a specific provider, falling through on failure."""
        attempted = list(attempted) + [provider_name]
        adapter = self.provider_manager.get(provider_name)
        if not adapter or not adapter.health_check():
            return self._fallback(prompt, system_prompt, temperature, max_tokens, "fallback", attempted)

        self.quota.record_request(provider_name)
        response = adapter.execute(prompt, system_prompt, temperature, max_tokens)

        if response.error:
            self.quota.record_error(provider_name)
            logger.warning(f"Provider '{provider_name}' failed: {response.error}")
            return self._fallback(prompt, system_prompt, temperature, max_tokens, "fallback", attempted)

        return response

    def _fallback(self, prompt: str, system_prompt: str, temperature: float,
                  max_tokens: int, task_type: str,
                  attempted: List[str]) -> NormalizedResponse:
        """Try next eligible provider in priority order."""
        all_providers = self.provider_manager.all()
        for name in all_providers:
            if name in attempted:
                continue
            adapter = self.provider_manager.get(name)
            if adapter and adapter.health_check():
                self.quota.record_request(name)
                response = adapter.execute(prompt, system_prompt, temperature, max_tokens)
                if not response.error:
                    return response
                self.quota.record_error(name)
                attempted.append(name)

        return NormalizedResponse(
            content="", provider="", model="",
            error=f"All providers failed. Attempted: {', '.join(attempted)}",
        )

    def health_summary(self) -> dict:
        """Full health summary across all components."""
        return {
            "providers": self.provider_manager.summary(),
            "registry": self.registry.summary(),
            "quota": self.quota.summary(),
            "adapter_count": self.provider_manager.count_healthy(),
            "registry_count": self.registry.count_active(),
        }
