"""
Platrixa — public developer configuration (Phase 12)
====================================================

A small, explicit, frozen configuration object for the public interface.

Design rules (Phase 12, section 4):

- Only configuration the existing runtime genuinely supports is exposed.
- No global mutable state; every ``Platrixa`` client owns its config.
- No new environment variables, API keys, auth, or hosted-service options.
- Model identity stays pinned by ``ProviderConfig`` defaults unless a caller
  explicitly supplies their own ``ProviderConfig``.

Every field maps onto an existing mechanism:

======================  ====================================================
Field                   Backed by
======================  ====================================================
provider                Kernel.model_provider() single selection point
                        ("auto" = today's exact env-driven behavior;
                        PLATRIXA_MODEL_ENDPOINT_URL set → remote)
endpoint_url            PLATRIXA_MODEL_ENDPOINT_URL
endpoint_token          PLATRIXA_MODEL_ENDPOINT_TOKEN
timeout                 PLATRIXA_MODEL_TIMEOUT
transport               PLATRIXA_MODEL_TRANSPORT ("gradio" → HF Space)
provider_config         backend.model_provider.base.ProviderConfig
rule_pack               Kernel(rule_pack=...) YAML path (Phase 10)
rule_hooks              Kernel(rule_hooks=...) Python hooks (Phase 10)
======================  ====================================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

# Hook objects are duck-typed here (Sequence of objects satisfying the
# backend.rules.contract.RuleHook protocol) so importing this module never
# needs to import the rules package. Validation happens at client
# construction, which does import the contract (cheap, stdlib-only).
HookLike = object

PROVIDERS = ("auto", "local", "remote")
TRANSPORTS = (None, "gradio")


@dataclass(frozen=True)
class PlatrixaConfig:
    """
    Explicit configuration for a ``Platrixa`` client.

    ``provider``:
        "auto"    — defer to the Kernel's single selection point
                    (PLATRIXA_MODEL_ENDPOINT_URL set → remote, else local).
                    This is the default and matches runtime behavior exactly.
        "local"   — force the local Hugging Face provider.
        "remote"  — force the remote provider (Modal endpoint, or the HF
                    Space when ``transport="gradio"``).

    ``endpoint_url`` / ``endpoint_token`` / ``timeout`` / ``transport``:
        Remote-provider connection settings. ``None`` means "use the
        existing environment defaults" — no new env vars are introduced.

    ``provider_config``:
        Optional ProviderConfig for model identity overrides. Leave ``None``
        to keep the pinned production base model + LoRA adapter revisions.

    ``rule_pack``:
        Optional path to a declarative YAML rule pack (Phase 10 format).
        A malformed pack fails closed at client construction.

    ``rule_hooks``:
        Optional sequence of RuleHook instances evaluated after YAML rules.
        Hooks may only downgrade states; they can never produce VERIFIED.
    """

    provider: str = "auto"
    endpoint_url: Optional[str] = None
    endpoint_token: Optional[str] = None
    timeout: Optional[float] = None
    transport: Optional[str] = None
    provider_config: Optional[object] = None  # ProviderConfig | None
    rule_pack: Optional[str] = None
    rule_hooks: Sequence[HookLike] = field(default_factory=tuple)

    def validate(self) -> None:
        """Fail closed on invalid configuration before any processing."""
        if self.provider not in PROVIDERS:
            raise ValueError(
                f"provider must be one of {PROVIDERS}, got {self.provider!r}"
            )
        if self.transport not in TRANSPORTS:
            raise ValueError(
                f"transport must be one of {TRANSPORTS}, got {self.transport!r}"
            )
        if self.rule_pack is not None and not isinstance(self.rule_pack, str):
            raise ValueError("rule_pack must be a filesystem path string or None")
        if self.rule_hooks:
            hooks = tuple(self.rule_hooks)
            for hook in hooks:
                rule_id = getattr(hook, "rule_id", None)
                if not isinstance(rule_id, str) or not rule_id.strip():
                    raise ValueError(
                        "each rule hook must define a non-empty 'rule_id' "
                        f"attribute; got {type(hook).__name__}"
                    )


__all__ = ["PlatrixaConfig", "PROVIDERS", "TRANSPORTS"]
