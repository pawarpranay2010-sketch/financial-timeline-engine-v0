"""
Platrixa — thin public facade over the Kernel (Phase 12)
========================================================

This module is deliberately boring: it configures and invokes the Kernel and
projects ``KernelResult`` for public consumption. It contains:

- NO model/provider logic
- NO accounting logic
- NO grounding logic
- NO rule-engine logic
- NO persistence logic
- NO status authority (VERIFIED is decided inside Kernel.process only)

Public surface defined in ``platrixa/__init__.py``; ``Platrixa`` here is the
single client class. The CLI (``platrixa/__main__.py``) and any other callers
go through ``Platrixa.process`` — never around it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from .config import PlatrixaConfig
from .errors import InputError, ProviderError  # noqa: F401 — re-exported here


class Platrixa:
    """
    The public developer entry point.

    Typical use::

        from platrixa import Platrixa

        client = Platrixa()                       # auto provider selection
        result = client.process("Purchased furniture for cash ₹15,000")
        print(result.status)                      # VERIFIED / REVIEW_REQUIRED / ...

    The client holds an explicitly-constructed Kernel; ``process`` forwards
    to ``Kernel.process`` exactly once per call.
    """

    def __init__(self, config: Optional[PlatrixaConfig] = None) -> None:
        self.config = config or PlatrixaConfig()
        # Validate cheaply and fail closed before building anything heavy.
        self.config.validate()
        self._kernel = self._build_kernel(self.config)

    # ------------------------------------------------------------------
    # Kernel construction (configuration only — no business logic)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_kernel(config: PlatrixaConfig) -> Any:
        """
        Construct the Kernel according to the configuration.

        - "auto":  defer to the Kernel's own single provider-selection point
                    (identical to ``Kernel()`` today — env-driven).
        - "local"/"remote": construct the matching provider explicitly with
                    the existing provider classes and pass it via
                    ``Kernel(model_provider=...)``.

        Rule pack / hooks are forwarded to the Kernel, which enforces the
        Phase 10 authority invariants (downgrade-only, fail-closed
        construction on malformed packs).
        """
        # Local imports keep `import platrixa` cheap (no torch/requests at
        # import time) and preserve the Kernel's lazy discipline.
        from backend.kernel.kernel import Kernel

        if config.provider == "auto":
            return Kernel(
                rule_pack=config.rule_pack,
                rule_hooks=tuple(config.rule_hooks) if config.rule_hooks else (),
            )

        if config.provider == "local":
            from backend.model_provider.local_hf import LocalHFModelProvider

            provider = LocalHFModelProvider(
                config=config.provider_config  # None → pinned defaults
                if config.provider_config is not None
                else None
            )
        else:  # "remote"
            from backend.model_provider.remote_hf import RemoteHFModelProvider

            provider = RemoteHFModelProvider(
                config=config.provider_config,  # None → pinned defaults
                url=config.endpoint_url,
                token=config.endpoint_token,
                timeout=config.timeout,
            )
            if config.transport == "gradio":
                from backend.model_provider.hf_gradio import HFGradioModelProvider

                provider = HFGradioModelProvider(
                    config=config.provider_config,
                    url=config.endpoint_url,
                    token=config.endpoint_token,
                    timeout=config.timeout,
                )

        return Kernel(
            model_provider=provider,
            rule_pack=config.rule_pack,
            rule_hooks=tuple(config.rule_hooks) if config.rule_hooks else (),
        )

    # ------------------------------------------------------------------
    # Single processing entry point
    # ------------------------------------------------------------------

    def process(
        self,
        input: Any,
        *,
        request_id: Optional[str] = None,
    ) -> Any:
        """
        Process one financial transaction through the Kernel.

        ``input`` must be the canonical contract: a raw transaction string
        (1–2000 characters after stripping), exactly what the HTTP API's
        ``KernelProcessRequest`` accepts. This is the runtime's cleanly
        supported input form.

        Returns a ``PlatrixaResult`` — a read-only projection of the
        authoritative ``KernelResult``. State (including VERIFIED) is never
        recomputed here.

        Raises ``InputError`` for non-string or oversized input (input-shape
        validation only — no accounting semantics).
        """
        from backend.model_provider.base import ModelProviderError

        if not isinstance(input, str):
            raise InputError(
                f"input must be a raw transaction string, got {type(input).__name__}"
            )
        text = input.strip()
        if not text:
            raise InputError("input must be a non-empty transaction string")
        if len(text) > _MAX_INPUT_LENGTH:
            raise InputError(
                f"input exceeds the {_MAX_INPUT_LENGTH}-character contract "
                f"({len(text)} characters)"
            )

        try:
            result = self._kernel.process(text, request_id=request_id)
        except ModelProviderError as exc:
            # Provider exceptions are runtime failures, not successes: wrap
            # with the original cause preserved (fail closed, no swallow).
            raise ProviderError(
                f"model provider failed: {type(exc).__name__}: {exc}"
            ) from exc

        return PlatrixaResult(result)

    # ------------------------------------------------------------------
    # Introspection helpers (configuration/status views only)
    # ------------------------------------------------------------------

    def provider_status(self) -> Dict[str, Any]:
        """Non-secret provider status via the Kernel's provider boundary."""
        status = self._kernel.model_provider().status()
        return {
            "available": bool(getattr(status, "available", False)),
            "loadable": bool(getattr(status, "loadable", False)),
            "model_id": getattr(status, "model_id", ""),
            "adapter_repo_id": getattr(status, "adapter_repo_id", ""),
            "adapter_revision": getattr(status, "adapter_revision", ""),
            "reason": getattr(status, "reason", ""),
        }

    def rule_pack_summary(self) -> Optional[Dict[str, Any]]:
        """Summary of the configured rule pack, or None when not configured."""
        engine = self._kernel._get_rule_engine()
        if engine is None:
            return None
        return engine.describe()

    def kernel(self) -> Any:
        """
        Escape hatch exposing the underlying Kernel.

        Returning the Kernel itself (rather than wrapping it further) keeps
        this layer honest: there is exactly one runtime and no shadow copy
        of it. Advanced consumers may use it, but the documented public path
        is ``process()``.
        """
        return self._kernel


# ---------------------------------------------------------------------------
# Result projection
# ---------------------------------------------------------------------------

_MAX_INPUT_LENGTH = 2000  # mirrors KernelProcessRequest.raw_input


# ---------------------------------------------------------------------------
# JSON-safety projection (shared by PlatrixaResult.to_dict and the CLI)
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """
    Recursively convert a Kernel result structure into JSON-safe values.

    Explicit, small conversion table — deliberate and documented, not a
    blanket ``str()`` over everything:

    - Decimal            → exact string ("25000.00") — deterministic, precise
    - datetime / date    → ISO-8601 string
    - bytes              → decoded UTF-8 (lossy only if not text)
    - set / frozenset    → sorted list (deterministic ordering)
    - tuple              → list
    - anything else      → passed through (the Kernel result is composed of
                           plain dicts/lists/strings/ints/bools/None)
    """
    from decimal import Decimal
    from datetime import date, datetime

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(item) for item in value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


class PlatrixaResult:
    """
    Read-only public projection of the authoritative ``KernelResult``.

    This is a view, not a re-computation: every property reads the Kernel's
    result. ``to_dict()`` serializes deterministically for JSON use
    (delegating to ``KernelResult.to_dict()`` plus the projection fields).
    """

    __slots__ = ("_result",)

    def __init__(self, result: Any) -> None:
        self._result = result

    @property
    def status(self) -> str:
        """Terminal state decided by the Kernel (never recomputed)."""
        return self._result.status

    @property
    def status_label(self) -> str:
        """Human-readable label for the state."""
        return self._result.status_label

    @property
    def success(self) -> bool:
        """True for states the runtime considers successful completions."""
        return bool(self._result.success)

    @property
    def interpretation(self) -> Optional[Dict[str, Any]]:
        """The schema-validated 18-field interpretation candidate."""
        return self._result.interpretation_candidate

    @property
    def accounting(self) -> Optional[Dict[str, Any]]:
        """The deterministic accounting result produced by the Kernel."""
        return self._result.accounting_result

    @property
    def issues(self) -> list:
        """Kernel-level issues (including fail-closed failure reasons)."""
        return list(self._result.issues or [])

    @property
    def grounding_issues(self) -> list:
        """Grounding/verification issues when the state is GROUNDING_FAILED."""
        return list(self._result.grounding_issues or [])

    @property
    def rule_evidence(self) -> list:
        """Structured evidence from the optional rule boundary (Phase 10)."""
        return list(self._result.rule_evidence or [])

    @property
    def request_id(self) -> str:
        return self._result.request_id

    @property
    def raw_input(self) -> str:
        return self._result.raw_input

    @property
    def metadata(self) -> Dict[str, Any]:
        return dict(self._result.metadata or {})

    @property
    def next_action(self) -> Optional[str]:
        return self._result.next_action

    @property
    def kernel_result(self) -> Any:
        """The underlying authoritative KernelResult (advanced use)."""
        return self._result

    def to_dict(self) -> Dict[str, Any]:
        """
        Deterministic, JSON-safe serialization.

        Field names and shapes are stable: they are exactly the Kernel's own
        ``to_dict()`` fields — no Python reprs, no objects, no secrets (the
        Kernel result contains none).

        The one projection rule: ``Decimal`` values produced by the
        deterministic accounting kernel are serialized as their exact string
        representation (e.g. "25000.00") — precise, deterministic, and free
        of float round-trip error. All other types are JSON-native.
        """
        return _jsonable(self._result.to_dict())

    def __repr__(self) -> str:  # pragma: no cover - presentation only
        return (
            f"PlatrixaResult(status={self.status!r}, "
            f"success={self.success!r}, request_id={self.request_id!r})"
        )
