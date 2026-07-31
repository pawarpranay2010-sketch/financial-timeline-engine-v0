"""AI Executive Gateway — Provider Integration Layer

This package owns every external AI provider call in the Financial Timeline
Engine. No other module (app.py, intelligence/, module4/) should import
provider SDKs or construct API requests directly.

Architecture:

  Request
    → AdmissionController (token budget, rate-limit check)
    → Router (workload-aware provider/model selection)
    → AIExecutive (execute, normalize, fallback)
    → NormalizedResponse (unified output)

Providers (9 active):
  - Google AI Studio (Gemini)
  - Groq
  - OpenRouter
  - NVIDIA (Nemotron)
  - RapidAPI
  - SambaNova
  - GitHub Models
  - Cerebras
  - Cohere

Removed:
  - Together AI (removed)
  - Fireworks AI (removed)
"""
from .normalized_response import NormalizedResponse
from .provider_adapter import ProviderAdapter, ProviderCapability
from .provider_manager import ProviderManager
from .router import Router, RouteDecision
from .admission_controller import AdmissionController, AdmissionResult
from .ai_executive import AIExecutive
from .redis_quota import RedisQuotaTracker
from .capability_registry import CapabilityRegistry, ProviderMetadata

__all__ = [
    "NormalizedResponse",
    "ProviderAdapter",
    "ProviderCapability",
    "ProviderManager",
    "Router",
    "RouteDecision",
    "AdmissionController",
    "AdmissionResult",
    "AIExecutive",
    "RedisQuotaTracker",
    "CapabilityRegistry",
    "ProviderMetadata",
]
