"""ProviderManager — registration, health, lifecycle for all AI providers."""
import logging
from typing import Dict, Optional, List
from core.config import get_secret
from .provider_adapter import ProviderAdapter
from .providers import (
    GoogleAdapter, GroqAdapter, OpenRouterAdapter,
    NvidiaAdapter, RapidAPIAdapter, SambaNovaAdapter,
    GitHubAdapter, CerebrasAdapter, CohereAdapter,
)

logger = logging.getLogger(__name__)


class ProviderManager:
    """Manages all AI provider adapters — registration, health, lifecycle.

    Loads API keys from environment variables. Together AI and Fireworks AI
    have been removed from this registry.
    """

    ENV_KEY_MAP = {
        "google": "GOOGLE_API_KEY",
        "groq": "GROQ_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "nvidia": "NVIDIA_API_KEY",
        "rapidapi": "RAPIDAPI_KEY",
        "sambanova": "SAMBANOVA_API_KEY",
        "github": "GITHUB_TOKEN",
        "cerebras": "CEREBRAS_API_KEY",
        "cohere": "COHERE_API_KEY",
    }

    ADAPTER_MAP = {
        "google": GoogleAdapter,
        "groq": GroqAdapter,
        "openrouter": OpenRouterAdapter,
        "nvidia": NvidiaAdapter,
        "rapidapi": RapidAPIAdapter,
        "sambanova": SambaNovaAdapter,
        "github": GitHubAdapter,
        "cerebras": CerebrasAdapter,
        "cohere": CohereAdapter,
    }

    DEFAULT_PRIORITY = [
        "google", "groq", "openrouter", "nvidia", "rapidapi",
        "sambanova", "github", "cerebras", "cohere",
    ]

    def __init__(self):
        self._adapters: Dict[str, ProviderAdapter] = {}
        self._health: Dict[str, bool] = {}
        self._key_status: Dict[str, bool] = {}
        self._initialize()

    def _initialize(self) -> None:
        for name in self.DEFAULT_PRIORITY:
            env_key = self.ENV_KEY_MAP.get(name, "")
            api_key = get_secret(env_key, "")
            self._key_status[name] = bool(api_key)
            if api_key:
                adapter_cls = self.ADAPTER_MAP.get(name)
                if adapter_cls:
                    try:
                        adapter = adapter_cls(api_key=api_key)
                        self._adapters[name] = adapter
                        self._health[name] = True
                        logger.info(f"Provider '{name}' registered (key: {env_key} {'✅' if api_key else '❌'})")
                    except Exception as e:
                        logger.warning(f"Provider '{name}' init failed: {e}")
                        self._health[name] = False

    def get(self, name: str) -> Optional[ProviderAdapter]:
        return self._adapters.get(name)

    def all(self) -> List[str]:
        return list(self._adapters.keys())

    def all_adapters(self) -> Dict[str, ProviderAdapter]:
        return dict(self._adapters)

    def is_healthy(self, name: str) -> bool:
        return self._health.get(name, False)

    def health_summary(self) -> Dict[str, bool]:
        return {
            name: adapter.health_check()
            for name, adapter in self._adapters.items()
        }

    def key_status(self) -> Dict[str, bool]:
        return dict(self._key_status)

    def count_healthy(self) -> int:
        return sum(1 for v in self.health_summary().values() if v)

    def summary(self) -> List[dict]:
        return [
            {
                "provider": name,
                "key_configured": self._key_status.get(name, False),
                "adapter_registered": name in self._adapters,
                "health_check": adapter.health_check(),
            }
            for name, adapter in self._adapters.items()
        ]
