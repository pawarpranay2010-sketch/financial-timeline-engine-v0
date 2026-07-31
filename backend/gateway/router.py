"""Router — workload-aware provider/model selection with deterministic rules.

Does NOT waste an LLM call to classify which provider to use. Uses
pre-configured capability metadata and routing rules.
"""
from typing import Optional, List
from dataclasses import dataclass, field
from .capability_registry import CapabilityRegistry, ProviderMetadata


# Task type constants
TASK_SIMPLE = "simple"                    # Basic Q&A, fast inference
TASK_FINANCIAL_ANALYSIS = "financial"     # Deep financial reasoning
TASK_RAG = "rag"                          # Retrieval + generation
TASK_LONG_CONTEXT = "long_context"        # Large document processing
TASK_STRUCTURED = "structured"            # JSON structured output
TASK_FALLBACK = "fallback"               # Last resort


@dataclass
class RouteDecision:
    """Decision from the Router."""

    provider: str
    model: str
    capability: ProviderMetadata
    task_type: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return {"provider": self.provider, "model": self.model,
                "task_type": self.task_type, "reason": self.reason}


class Router:
    """Deterministic workload-aware router.

    Uses capability metadata and task requirements to select the best
    provider without an LLM call.
    """

    def __init__(self, registry: CapabilityRegistry):
        self._registry = registry

    def route(self, task_type: str, estimated_input_tokens: int = 0,
              estimated_output_tokens: int = 4096) -> Optional[RouteDecision]:
        """Route a task to the most suitable provider."""
        if task_type == TASK_SIMPLE:
            return self._route_simple()
        elif task_type == TASK_FINANCIAL_ANALYSIS:
            return self._route_financial()
        elif task_type == TASK_RAG:
            return self._route_rag(estimated_input_tokens)
        elif task_type == TASK_LONG_CONTEXT:
            return self._route_long_context(estimated_input_tokens)
        elif task_type == TASK_STRUCTURED:
            return self._route_structured()
        elif task_type == TASK_FALLBACK:
            return self._route_fallback()
        else:
            return self._route_simple()

    def _route_simple(self) -> Optional[RouteDecision]:
        """Fast, simple tasks → lowest-latency provider."""
        provider = self._registry.find_fastest(min_reasoning=1)
        if not provider:
            return None
        return RouteDecision(
            provider=provider.provider, model=provider.model,
            capability=provider, task_type=TASK_SIMPLE,
            reason=f"Fastest provider ({provider.capabilities.expected_latency_ms}ms expected)",
        )

    def _route_financial(self) -> Optional[RouteDecision]:
        """Financial analysis → highest reasoning level."""
        candidates = self._registry.find_by_capability(
            min_reasoning=2, requires_financial=True
        )
        if not candidates:
            fallback = self._route_fallback()
            return fallback
        # Pick highest reasoning, then lowest latency
        best = max(candidates, key=lambda p: (
            p.capabilities.reasoning_level,
            -p.capabilities.expected_latency_ms,
        ))
        return RouteDecision(
            provider=best.provider, model=best.model,
            capability=best, task_type=TASK_FINANCIAL_ANALYSIS,
            reason=f"Best financial reasoning (level {best.capabilities.reasoning_level})",
        )

    def _route_rag(self, estimated_tokens: int) -> Optional[RouteDecision]:
        """RAG tasks → providers with sufficient context + structured output."""
        candidates = self._registry.find_by_capability(
            requires_rag=True, min_context=estimated_tokens
        )
        if not candidates:
            return self._route_long_context(estimated_tokens)
        best = min(candidates, key=lambda p: p.capabilities.expected_latency_ms)
        return RouteDecision(
            provider=best.provider, model=best.model,
            capability=best, task_type=TASK_RAG,
            reason=f"RAG-eligible, {best.capabilities.context_window} context",
        )

    def _route_long_context(self, estimated_tokens: int) -> Optional[RouteDecision]:
        """Large docs → providers with large context windows."""
        candidates = self._registry.find_by_capability(
            requires_long_context=True, min_context=estimated_tokens
        )
        if not candidates:
            # Fall back to max available
            candidates = sorted(
                self._registry.all(),
                key=lambda p: p.capabilities.context_window,
                reverse=True,
            )
        if not candidates:
            return None
        best = candidates[0]
        return RouteDecision(
            provider=best.provider, model=best.model,
            capability=best, task_type=TASK_LONG_CONTEXT,
            reason=f"Largest context ({best.capabilities.context_window} tokens)",
        )

    def _route_structured(self) -> Optional[RouteDecision]:
        """Structured output → providers supporting it."""
        candidates = self._registry.find_by_capability(requires_structured=True)
        if not candidates:
            return None
        best = min(candidates, key=lambda p: p.capabilities.expected_latency_ms)
        return RouteDecision(
            provider=best.provider, model=best.model,
            capability=best, task_type=TASK_STRUCTURED,
            reason="Supports structured output",
        )

    def _route_fallback(self) -> Optional[RouteDecision]:
        """Last resort — any active provider."""
        all_providers = self._registry.all()
        if not all_providers:
            return None
        # Pick highest priority (lowest number)
        best = min(all_providers, key=lambda p: p.priority)
        return RouteDecision(
            provider=best.provider, model=best.model,
            capability=best, task_type=TASK_FALLBACK,
            reason="Fallback — any available provider",
        )
