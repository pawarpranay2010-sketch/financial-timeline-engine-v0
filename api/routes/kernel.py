"""Platrixa — Deterministic Kernel API route (Phase 7F).

The authoritative HTTP boundary for the deterministic Kernel workflow:

    HTTP request
        ↓
    KernelProcessRequest          (request-shape validation only)
        ↓
    Kernel.process(raw_input)     (single application reasoning boundary)
        ↓
    KernelResult
        ↓
    ResultPersistence (Phase 7E contract)
        ↓
    PersistedResult / PersistenceFailure
        ↓
    KernelProcessResponse         (safe KernelResult projection)
        ↓
    HTTP response

Responsibility rules enforced here:

  * This route performs NO accounting reasoning: no transaction parsing,
    no debit/credit assignment, no journal generation, no grounding, no
    schema validation beyond HTTP request-shape checks.
  * Kernel remains the single owner of the application accounting workflow.
  * Terminal status taxonomy is preserved verbatim — failures are never
    collapsed into a generic 500.
  * Persistence uses the Phase 7E contract; persistence failure is
    represented explicitly and never mutates the KernelResult.
  * No ML internals (transformers/peft/Hugging Face) are exposed.
  * Heavy dependencies (Kernel, persistence implementation) are imported
    lazily inside the dependency functions, so importing api.main never
    pulls in torch/transformers or requires DATABASE_URL.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from api.schemas import (
    KernelProcessRequest,
    KernelProcessResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kernel"])

# ---------------------------------------------------------------------------
# HTTP status mapping (Kernel terminal state → HTTP transport status).
#
# This is transport mapping, NOT accounting reinterpretation: the response
# body always carries the authoritative Kernel status verbatim.
# ---------------------------------------------------------------------------

_HTTP_STATUS_BY_KERNEL_STATUS: Dict[str, int] = {
    "VERIFIED": 200,
    "REVIEW_REQUIRED": 200,
    # BLOCKED is an existing accounting-authority outcome (safety boundary),
    # a legitimate deterministic result rather than a transport failure.
    "BLOCKED": 200,
    "VALIDATION_FAILED": 422,
    "GROUNDING_FAILED": 422,
    "FORBIDDEN_OUTPUT": 422,
    "MODEL_UNAVAILABLE": 503,
    "UNSUPPORTED_TRANSACTION": 422,
}

# Terminal states that must never be reported as persisted VERIFIED.
_NON_SUCCESS_STATUSES = {
    "VALIDATION_FAILED",
    "GROUNDING_FAILED",
    "FORBIDDEN_OUTPUT",
    "MODEL_UNAVAILABLE",
    "UNSUPPORTED_TRANSACTION",
}


# ---------------------------------------------------------------------------
# Lazy dependency wiring (import-safe: no torch, no DATABASE_URL at import)
# ---------------------------------------------------------------------------


def _get_persistence():
    """
    Resolve the Phase 7E ResultPersistence implementation.

    Default is the concrete PostgreSQL implementation, loaded lazily so the
    API module never imports database infrastructure at import time.
    Tests may override this via set_persistence().
    """
    if getattr(_get_persistence, "_override", None) is not None:
        return _get_persistence._override
    from backend.persistence.postgres import PostgresResultPersistence

    return PostgresResultPersistence()


def set_persistence(persistence: Any) -> None:
    """Inject a persistence implementation (tests / alternative backends)."""
    _get_persistence._override = persistence


def reset_persistence() -> None:
    """Remove any injected persistence override."""
    _get_persistence._override = None


def _get_kernel():
    """Resolve the Kernel (lazily; tests may inject a stub)."""
    override = getattr(_get_kernel, "_override", None)
    if override is not None:
        return override
    from backend.kernel.kernel import Kernel

    return Kernel()


def set_kernel(kernel: Any) -> None:
    """Inject a Kernel (tests / alternative wiring)."""
    _get_kernel._override = kernel


def reset_kernel() -> None:
    """Remove any injected Kernel override."""
    _get_kernel._override = None


# ---------------------------------------------------------------------------
# Safe response projection (no ML internals, no hidden reasoning)
# ---------------------------------------------------------------------------

# Keys excluded from the accounting-result projection: any model/ML metadata
# that could leak implementation details into the API surface.
_LEAKY_ACCOUNTING_KEYS = {
    "model_id",
    "provider_revision",
    "adapter_revision",
    "raw_model_output",
    "generated_profile",
    "reasoning",
    "chain_of_thought",
    "raw_response",
}


def _safe_accounting(accounting: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Project the accounting result verbatim minus leak-prone keys."""
    if accounting is None:
        return None
    if not isinstance(accounting, dict):
        return None
    return {k: v for k, v in accounting.items() if k not in _LEAKY_ACCOUNTING_KEYS}


def _safe_candidate(candidate: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Project the 18-field interpretation candidate minus raw-model internals.
    The candidate is the schema-validated semantic structure — already safe —
    but we still strip anything that smells like model internals.
    """
    if candidate is None:
        return None
    if not isinstance(candidate, dict):
        return None
    return {k: v for k, v in candidate.items() if k not in _LEAKY_ACCOUNTING_KEYS}


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.post("/kernel/process", response_model=KernelProcessResponse)
def process_transaction(payload: KernelProcessRequest, request: Request):
    """
    Process one student transaction through the Kernel boundary.

    Single authoritative path:

        request schema → Kernel.process() → KernelResult
        → Phase 7E persistence boundary → response schema

    Status mapping (body carries the Kernel status verbatim):
        VERIFIED / REVIEW_REQUIRED     → 200
        VALIDATION_FAILED / GROUNDING_FAILED / FORBIDDEN_OUTPUT /
        UNSUPPORTED_TRANSACTION        → 422
        MODEL_UNAVAILABLE              → 503
        persistence failure            → 502 with persisted=false
    """
    raw_input = payload.raw_input

    kernel = _get_kernel()
    result = kernel.process(raw_input)

    # Snapshot the authoritative fields BEFORE any persistence call, so the
    # response can never be affected by storage-side state.
    pre_status = result.status
    pre_accounting = result.accounting_result
    pre_candidate = result.interpretation_candidate
    pre_issues = list(result.issues or [])
    pre_grounding = list(result.grounding_issues or [])

    # ------------------------------------------------------------------
    # Persistence through the Phase 7E boundary (contract, not concrete)
    # ------------------------------------------------------------------
    persisted = False
    persistence_error: Optional[Dict[str, str]] = None
    try:
        persistence = _get_persistence()
        outcome = persistence.persist(result)

        from backend.persistence.base import PersistenceFailure

        if isinstance(outcome, PersistenceFailure):
            persistence_error = {
                "kind": outcome.kind,
                "reason": outcome.reason,
            }
            logger.warning(
                "kernel persistence failed (status=%s kind=%s)",
                pre_status,
                outcome.kind,
            )
        else:
            persisted = True
    except Exception as exc:  # defensive: never leak storage internals
        persistence_error = {
            "kind": "PERSISTENCE_WRITE_FAILED",
            "reason": "unexpected persistence error",
        }
        logger.warning("kernel persistence raised: %s", exc)

    response = KernelProcessResponse(
        request_id=getattr(result, "request_id", None),
        status=pre_status,
        status_label=getattr(result, "status_label", "") or pre_status,
        success=(pre_status == "VERIFIED"),
        next_action=getattr(result, "next_action", "") or None,
        issues=pre_issues,
        grounding_issues=pre_grounding,
        verification_status=getattr(result, "verification_status", None),
        interpretation=_safe_candidate(pre_candidate),
        accounting=_safe_accounting(pre_accounting),
        persisted=persisted,
        persistence_error=persistence_error,
    )

    transport_status = _HTTP_STATUS_BY_KERNEL_STATUS.get(pre_status, 500)
    if persistence_error is not None:
        # Explicit, distinct representation: accounting status preserved in
        # the body, transport signals the storage problem.
        if transport_status < 400:
            transport_status = 502
    return _json_response(response, transport_status)


def _json_response(response: KernelProcessResponse, status_code: int):
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    # jsonable_encoder converts Decimal amounts emitted by the deterministic
    # accounting kernel into JSON-safe values (exactly the conversion FastAPI
    # itself applies to response_model payloads). The KernelResult and the
    # accounting shape are untouched — this is HTTP-edge serialization only.
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(response.model_dump()),
    )
