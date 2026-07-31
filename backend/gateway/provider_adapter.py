"""Abstract base class for all AI provider adapters."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from .normalized_response import NormalizedResponse


@dataclass
class ProviderCapability:
    """Describes what a provider/model can do."""
    provider: str
    model: str
    reasoning_level: int = 1           # 1=basic, 2=intermediate, 3=deep
    context_window: int = 8192
    expected_latency_ms: float = 2000
    supports_structured_output: bool = False
    supports_tool_use: bool = False
    supports_function_calling: bool = False
    supports_financial_analysis: bool = False
    supports_rag: bool = False
    supports_long_context: bool = False
    estimated_cost_per_1k_tokens: float = 0.0
    rpm_limit: Optional[int] = None
    tpm_limit: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "reasoning_level": self.reasoning_level,
            "context_window": self.context_window,
            "expected_latency_ms": self.expected_latency_ms,
            "supports_structured_output": self.supports_structured_output,
            "supports_financial_analysis": self.supports_financial_analysis,
            "supports_rag": self.supports_rag,
            "supports_long_context": self.supports_long_context,
        }


class ProviderAdapter(ABC):
    """Every AI provider adapter must implement this interface."""

    def __init__(self, api_key: str = ""):
        self._api_key = api_key

    @abstractmethod
    def execute(self, prompt: str, system_prompt: str = "",
                temperature: float = 0.3, max_tokens: int = 4096) -> NormalizedResponse:
        ...

    @abstractmethod
    def capability(self) -> ProviderCapability:
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__.replace("Adapter", "").lower()

    def health_check(self) -> bool:
        """Check if this adapter can make requests (key present, basic config ok)."""
        return bool(self._api_key)
