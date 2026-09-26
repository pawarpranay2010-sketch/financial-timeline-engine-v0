"""Platrixa — six-state public API status contract (Phase 5A).

This module is the SINGLE, deterministic mapping between the engine's
internal terminal-state taxonomy and the six-state public developer API
status. It is transport-layer only: it contains no accounting, grounding,
or financial semantics, and it can never CREATE, UPGRADE, or SOFTEN an
engine outcome — it only re-labels it for the public contract.

Architecture (Phase 5A §5):

    existing Platrixa processing facade
        ↓
    existing schema/grounding/authority pipeline
        ↓
    engine terminal state  (authoritative, never changed)
        ↓
    API-LEVEL status (six-state contract)  ← this module, mapping only
        ↓
    stable v1 JSON response

The six public states:

    PROCESSING        a request was admitted but the authoritative
                      result is not available (provider unavailable /
                      runtime failure). The client should treat the
                      outcome as unknown and retry later.
    VERIFIED          deterministic execution passed (engine VERIFIED).
    REVIEW_REQUIRED   valid but flagged for human review (engine
                      REVIEW_REQUIRED, including rule-pack downgrades).
    UNSUPPORTED       no deterministic authority could accept the input
                      (engine UNSUPPORTED_TRANSACTION / BLOCKED /
                      FORBIDDEN_OUTPUT).
    INVALID_INPUT     malformed transport request or input rejected by
                      the public input contract (400/413/415 error paths).
    FAILED            internal deterministic rejection with the reason
                      recorded in issues/grounding_issues (engine
                      VALIDATION_FAILED / GROUNDING_FAILED) or an
                      unexpected server-side failure (500).

Core principle preserved: RECOGNITION ≠ AUTHORITY — and here, TRANSPORT ≠
AUTHORITY. The HTTP layer re-labels the engine's own terminal state; the
response always also carries the verbatim engine state so no information
is lost:

    { "status": "REVIEW_REQUIRED",      ← six-state public contract
      "engine_status": "REVIEW_REQUIRED" ← verbatim engine terminal state }

Design rules enforced by the engine-status map below:

  * The map is total over the engine's terminal taxonomy — an unknown
    engine state fails closed to FAILED rather than guessing.
  * No engine state maps to VERIFIED except VERIFIED itself.
  * No probabilistic confidence participates in the mapping; it is a
    pure, total function of the engine status string.
"""

from __future__ import annotations

from typing import Dict, Final, Optional

# ---------------------------------------------------------------------------
# The six public states (closed vocabulary — never extended at runtime)
# ---------------------------------------------------------------------------

STATUS_PROCESSING: Final = "PROCESSING"
STATUS_VERIFIED: Final = "VERIFIED"
STATUS_REVIEW_REQUIRED: Final = "REVIEW_REQUIRED"
STATUS_UNSUPPORTED: Final = "UNSUPPORTED"
STATUS_INVALID_INPUT: Final = "INVALID_INPUT"
STATUS_FAILED: Final = "FAILED"

PUBLIC_STATES: Final = (
    STATUS_PROCESSING,
    STATUS_VERIFIED,
    STATUS_REVIEW_REQUIRED,
    STATUS_UNSUPPORTED,
    STATUS_INVALID_INPUT,
    STATUS_FAILED,
)

# Engine terminal state → six-state public status.
# The engine taxonomy is the authoritative internal representation; this
# table adds a public label WITHOUT removing or weakening any state.
STATUS_BY_ENGINE_STATE: Final[Dict[str, str]] = {
    # deterministic execution passed — the only VERIFIED source
    "VERIFIED": STATUS_VERIFIED,
    # valid but flagged for human review (incl. rule-pack downgrades)
    "REVIEW_REQUIRED": STATUS_REVIEW_REQUIRED,
    # safety-boundary rejection: no authority could accept it
    "BLOCKED": STATUS_UNSUPPORTED,
    # capability unavailable: no supported accounting capability
    "UNSUPPORTED_TRANSACTION": STATUS_UNSUPPORTED,
    # forbidden structure rejected by the authority boundary
    "FORBIDDEN_OUTPUT": STATUS_UNSUPPORTED,
    # deterministic rejection with reason recorded in issues
    "VALIDATION_FAILED": STATUS_FAILED,
    "GROUNDING_FAILED": STATUS_FAILED,
    # provider/runtime unavailability: outcome unknown → PROCESSING
    "MODEL_UNAVAILABLE": STATUS_PROCESSING,
}

# Transport-level error codes → six-state public status. These are the
# request-transport rejections handled at the HTTP boundary before any
# engine invocation (and therefore before any accounting ran).
STATUS_BY_ERROR_CODE: Final[Dict[str, str]] = {
    "REQUEST_MALFORMED": STATUS_INVALID_INPUT,
    "REQUEST_TOO_LARGE": STATUS_INVALID_INPUT,
    "INPUT_MISSING": STATUS_INVALID_INPUT,
    "INPUT_AMBIGUOUS": STATUS_INVALID_INPUT,
    "FILE_EMPTY": STATUS_INVALID_INPUT,
    "FILE_NAME_MISSING": STATUS_INVALID_INPUT,
    "FILE_TYPE_UNSUPPORTED": STATUS_INVALID_INPUT,
    "CONTENT_TYPE_UNSUPPORTED": STATUS_INVALID_INPUT,
    "FILE_TOO_LARGE": STATUS_INVALID_INPUT,
    "UNAUTHORIZED": STATUS_INVALID_INPUT,
    "QUOTA_EXHAUSTED": STATUS_INVALID_INPUT,
    "METERING_UNAVAILABLE": STATUS_PROCESSING,
    "PROVIDER_UNAVAILABLE": STATUS_PROCESSING,
    "INPUT_INVALID": STATUS_INVALID_INPUT,
    # Phase 5C idempotency transport codes:
    #   key format/size problems and key-reuse conflicts are client
    #   errors (INVALID_INPUT); an unavailable idempotency store fails
    #   closed like metering (PROCESSING = retryable, not yet admitted).
    "IDEMPOTENCY_KEY_INVALID": STATUS_INVALID_INPUT,
    "IDEMPOTENCY_KEY_TOO_LONG": STATUS_INVALID_INPUT,
    "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST": STATUS_INVALID_INPUT,
    # deployment lacks the durable store entirely → client-side config
    # issue, not a transient outage (no point retrying)
    "IDEMPOTENCY_NOT_CONFIGURED": STATUS_INVALID_INPUT,
    # transient store failure while metering IS configured → fail closed
    "IDEMPOTENCY_UNAVAILABLE": STATUS_PROCESSING,
}

# Human-readable labels for the six public states.
LABEL_BY_PUBLIC_STATUS: Final[Dict[str, str]] = {
    STATUS_PROCESSING: "Processing",
    STATUS_VERIFIED: "Verified",
    STATUS_REVIEW_REQUIRED: "Review required",
    STATUS_UNSUPPORTED: "Unsupported",
    STATUS_INVALID_INPUT: "Invalid input",
    STATUS_FAILED: "Failed",
}

# Coarse retryability signal for integrators (advisory only).
RETRYABLE_BY_PUBLIC_STATUS: Final[Dict[str, bool]] = {
    STATUS_PROCESSING: True,
    STATUS_VERIFIED: False,
    STATUS_REVIEW_REQUIRED: False,
    STATUS_UNSUPPORTED: False,
    STATUS_INVALID_INPUT: False,
    STATUS_FAILED: False,
}

# Back-compat aliases for the pre-existing ``success`` field contract
# (``success is true`` only for VERIFIED).
_SUCCESS_BY_PUBLIC_STATUS: Final[Dict[str, bool]] = {
    STATUS_VERIFIED: True,
    STATUS_PROCESSING: False,
    STATUS_REVIEW_REQUIRED: False,
    STATUS_UNSUPPORTED: False,
    STATUS_INVALID_INPUT: False,
    STATUS_FAILED: False,
}

# Reason codes for the coarse public states, where the engine or the
# transport layer already has a machine-readable reason. These carry no
# new authority — they are stable public names for existing evidence.
REASON_BY_ENGINE_STATE: Final[Dict[str, str]] = {
    "BLOCKED": "SAFETY_BOUNDARY",
    "UNSUPPORTED_TRANSACTION": "NO_SUPPORTED_CAPABILITY",
    "FORBIDDEN_OUTPUT": "FORBIDDEN_STRUCTURE",
    "VALIDATION_FAILED": "VALIDATION_REJECTED",
    "GROUNDING_FAILED": "GROUNDING_REJECTED",
    "MODEL_UNAVAILABLE": "RESULT_PENDING",
}


def public_status_for_engine(engine_status: str) -> str:
    """Map an engine terminal state to its six-state public status.

    Total function: an unknown engine status fails CLOSED to FAILED
    rather than guessing or passing through unvalidated. Never raises.
    """
    if engine_status in STATUS_BY_ENGINE_STATE:
        return STATUS_BY_ENGINE_STATE[engine_status]
    return STATUS_FAILED


def public_status_for_error_code(error_code: str) -> str:
    """Map a transport error code to its six-state public status.

    Total function: unknown codes fail CLOSED to FAILED (a server-side
    transport failure), never to PROCESSING or VERIFIED. Never raises.
    """
    if error_code in STATUS_BY_ERROR_CODE:
        return STATUS_BY_ERROR_CODE[error_code]
    return STATUS_FAILED


def is_success_status(public_status: str) -> bool:
    """True only for the public VERIFIED state."""
    return public_status == STATUS_VERIFIED


def is_retryable_status(public_status: str) -> bool:
    """True when the caller may obtain a different outcome by retrying."""
    return bool(RETRYABLE_BY_PUBLIC_STATUS.get(public_status, False))


def engine_status_verbatim(engine_status: str) -> Optional[str]:
    """The engine terminal state to carry verbatim in API responses.

    Returns None for states that are not engine terminal states (pure
    transport errors carry their machine-readable error code instead).
    """
    if engine_status in STATUS_BY_ENGINE_STATE:
        return engine_status
    return None


def reason_code_for_engine(engine_status: str) -> Optional[str]:
    """Stable public reason code for a failed/unsupported engine state."""
    return REASON_BY_ENGINE_STATE.get(engine_status)
