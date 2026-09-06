"""
Platrixa — Kernel result contract (Phase 7C)

Public import surface for KernelResult and the Kernel terminal states.

This exists so later API/Kernel consumers can import from one obvious place
without reaching into implementation details.
"""

from __future__ import annotations

from backend.kernel.kernel import (
    FORBIDDEN_OUTPUT,
    GROUNDING_FAILED,
    Kernel,
    KernelResult,
    MODEL_UNAVAILABLE,
    UNSUPPORTED_TRANSACTION,
    VALIDATION_FAILED,
    __all__ as _kernel_all,
)

# Re-export the result types explicitly so backend.kernel.result is the natural
# home for contracts. The kernel.py implementation remains the runtime source.
__all__ = [
    "Kernel",
    "KernelResult",
    "MODEL_UNAVAILABLE",
    "VALIDATION_FAILED",
    "GROUNDING_FAILED",
    "FORBIDDEN_OUTPUT",
    "UNSUPPORTED_TRANSACTION",
]

__all__.extend([n for n in _kernel_all if n not in __all__])
