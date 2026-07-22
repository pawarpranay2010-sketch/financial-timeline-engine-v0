"""
Provider Factory

Returns the correct provider adapter.

Future providers can be added without changing business logic.
"""

from backend.module4.provider_manager import provider_manager


class ProviderFactory:

    @staticmethod
    def get(name: str):

        provider = provider_manager.get_provider(name)

        if provider is None:
            raise ValueError(f"Unknown provider: {name}")

        return provider
