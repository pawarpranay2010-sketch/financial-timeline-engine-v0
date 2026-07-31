"""Provider capability metadata registry — workload-aware routing."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .provider_adapter import ProviderCapability


@dataclass
class ProviderMetadata:
    """Extended provider metadata for routing decisions."""
    provider: str
    model: str
    capabilities: ProviderCapability
    priority: int = 100          # lower = tried first
    active: bool = True
    model_id: str = ""

    @property
    def is_available(self) -> bool:
        return self.active


class CapabilityRegistry:
    """Registry of all providers and their capabilities.

    Used by the Router to select an eligible provider for each task type.
    """

    def __init__(self):
        self._providers: Dict[str, ProviderMetadata] = {}

    def register(self, metadata: ProviderMetadata) -> None:
        self._providers[metadata.provider] = metadata

    def unregister(self, provider: str) -> None:
        if provider in self._providers:
            del self._providers[provider]

    def get(self, provider: str) -> Optional[ProviderMetadata]:
        return self._providers.get(provider)

    def all(self) -> List[ProviderMetadata]:
        return [p for p in self._providers.values() if p.active]

    def find_by_capability(self, *, min_reasoning: int = 1,
                           min_context: int = 0,
                           requires_structured: bool = False,
                           requires_financial: bool = False,
                           requires_rag: bool = False,
                           requires_long_context: bool = False) -> List[ProviderMetadata]:
        """Find eligible providers sorted by priority (lowest first)."""
        results = []
        for p in self._providers.values():
            if not p.active:
                continue
            c = p.capabilities
            if c.reasoning_level < min_reasoning:
                continue
            if c.context_window < min_context:
                continue
            if requires_structured and not c.supports_structured_output:
                continue
            if requires_financial and not c.supports_financial_analysis:
                continue
            if requires_rag and not c.supports_rag:
                continue
            if requires_long_context and not c.supports_long_context:
                continue
            results.append(p)
        return sorted(results, key=lambda x: x.priority)

    def find_fastest(self, min_reasoning: int = 1) -> Optional[ProviderMetadata]:
        """Find the fastest eligible provider."""
        candidates = self.find_by_capability(min_reasoning=min_reasoning)
        if not candidates:
            return None
        return min(candidates, key=lambda p: p.capabilities.expected_latency_ms)

    def find_best_reasoning(self) -> Optional[ProviderMetadata]:
        """Find the provider with highest reasoning level."""
        candidates = self.all()
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.capabilities.reasoning_level)

    def count_active(self) -> int:
        return sum(1 for p in self._providers.values() if p.active)

    def set_active(self, provider: str, active: bool) -> None:
        if provider in self._providers:
            self._providers[provider].active = active

    def summary(self) -> List[dict]:
        return [
            {
                "provider": p.provider,
                "model": p.model,
                "reasoning": p.capabilities.reasoning_level,
                "context": p.capabilities.context_window,
                "latency_ms": p.capabilities.expected_latency_ms,
                "active": p.active,
                "priority": p.priority,
                "structured": p.capabilities.supports_structured_output,
                "financial": p.capabilities.supports_financial_analysis,
                "rag": p.capabilities.supports_rag,
                "long_context": p.capabilities.supports_long_context,
            }
            for p in self._providers.values()
        ]
