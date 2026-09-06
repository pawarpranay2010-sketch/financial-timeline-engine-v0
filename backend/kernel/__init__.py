"""
Platrixa — Kernel boundary (Phase 7C)

Application-level orchestration boundary for the FYJC student-processing path.

See backend/kernel/kernel.py for the real implementation and
backend/kernel/result.py for the result contract surface.
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
)

__all__ = [
    "Kernel",
    "KernelResult",
    "MODEL_UNAVAILABLE",
    "VALIDATION_FAILED",
    "GROUNDING_FAILED",
    "FORBIDDEN_OUTPUT",
    "UNSUPPORTED_TRANSACTION",
]
