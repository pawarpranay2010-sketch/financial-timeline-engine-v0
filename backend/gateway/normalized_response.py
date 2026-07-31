"""Unified response structure for all AI providers.

Every provider adapter must return a NormalizedResponse. Downstream
financial modules must not need provider-specific code.
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class NormalizedResponse:
    """Normalized AI provider response."""

    content: str
    provider: str = ""
    model: str = ""
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    finish_reason: str = ""
    request_id: Optional[str] = None
    error: Optional[str] = None
    raw: Any = None

    @property
    def success(self) -> bool:
        return self.error is None and bool(self.content)

    @property
    def total_tokens(self) -> Optional[int]:
        if self.input_tokens is not None and self.output_tokens is not None:
            return self.input_tokens + self.output_tokens
        return None

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "finish_reason": self.finish_reason,
            "request_id": self.request_id,
            "error": self.error,
            "success": self.success,
        }
