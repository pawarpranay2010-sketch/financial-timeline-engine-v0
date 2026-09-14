"""
Platrixa — public error contract (Phase 12, section 6)
======================================================

A deliberately small, developer-facing error model.

- ``InputError`` — the caller's input violated the public input contract
  (non-string, empty, or over the 2000-character limit). This is raised
  before the Kernel runs; it is a caller error, not a processing failure.

- ``ProviderError`` — the model provider raised a runtime failure while the
  Kernel was processing. The original provider exception is always
  preserved as ``__cause__`` (``raise ... from exc``), so nothing is
  silently swallowed and full context remains available.

Everything else the runtime communicates travels in the result object as
fail-closed terminal states (MODEL_UNAVAILABLE, VALIDATION_FAILED,
GROUNDING_FAILED, FORBIDDEN_OUTPUT, UNSUPPORTED_TRANSACTION, BLOCKED,
REVIEW_REQUIRED) — operational failures are never converted into success.

Construction-time configuration errors raise standard ``ValueError`` /
``RulePackError`` (from ``backend.rules``) and are intentionally not
wrapped: a client that cannot be built correctly must fail loudly at
construction, matching the Kernel's fail-closed discipline.
"""

from __future__ import annotations

__all__ = ["PlatrixaError", "InputError", "ProviderError"]


class PlatrixaError(Exception):
    """Base class for public Platrixa interface errors."""


class InputError(PlatrixaError):
    """Public input contract violation (shape/size only — no semantics)."""


class ProviderError(PlatrixaError):
    """
    Model provider runtime failure during Kernel processing.

    ``__cause__`` preserves the original provider exception.
    """
