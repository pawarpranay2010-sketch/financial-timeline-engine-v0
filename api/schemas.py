"""Stage 2 — Pydantic request/response schemas."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    """Run the Agentic RAG pipeline for a company goal."""

    ticker: str = Field(..., min_length=1, max_length=20, examples=["AAPL"])
    goal: str = Field(
        ...,
        min_length=3,
        max_length=1000,
        examples=["Analyze AAPL's FY2024 revenue and net income"],
    )
    max_iterations: int = Field(default=3, ge=1, le=5)


class TickerRequest(BaseModel):
    """Fetch a market snapshot for a ticker."""

    ticker: str = Field(..., min_length=1, max_length=20, examples=["AAPL"])


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    stage: int
    uptime_seconds: float
    database: Dict[str, Any]
    redis: Dict[str, Any]
    providers: Dict[str, Any]


class ProviderStatus(BaseModel):
    name: str
    key_configured: bool
    env_var: str


class ProvidersResponse(BaseModel):
    status: str
    providers: List[ProviderStatus]
    financial_providers: Dict[str, Any]


class MarketSnapshotResponse(BaseModel):
    ticker: str
    success: bool
    data: Dict[str, Any]
    latency_ms: int
    error: Optional[str] = None


class AnalysisResponse(BaseModel):
    ticker: str
    goal: str
    terminal_state: str
    terminal_reason: Optional[str] = None
    iterations_used: int
    evidence_count: int
    resolved_count: int
    resolved_facts: List[Dict[str, Any]] = Field(default_factory=list)
    summary_text: str = ""


# ---------------------------------------------------------------------------
# Kernel boundary (Phase 7F)
# ---------------------------------------------------------------------------


class KernelProcessRequest(BaseModel):
    """
    Request for the authoritative Kernel workflow.

    Contains only what the Kernel actually needs: the student's raw input.
    The API performs request-shape validation only — no accounting,
    grounding, or schema-interpretation logic lives here.
    """

    raw_input: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Raw student transaction text, sent to the Kernel verbatim.",
    )


class KernelProcessResponse(BaseModel):
    """
    Safe projection of a KernelResult.

    The Kernel terminal status taxonomy is preserved verbatim:
        VERIFIED, REVIEW_REQUIRED, VALIDATION_FAILED, GROUNDING_FAILED,
        FORBIDDEN_OUTPUT, MODEL_UNAVAILABLE, UNSUPPORTED_TRANSACTION

    Failures are never collapsed into a generic error, and no ML/provider
    internals (transformers, peft, Hugging Face, raw model output) are
    exposed. Hidden chain-of-thought is never included.
    """

    request_id: Optional[str] = None
    status: str
    status_label: str = ""
    success: bool = False
    next_action: Optional[str] = None
    issues: List[str] = Field(default_factory=list)
    grounding_issues: List[str] = Field(default_factory=list)
    verification_status: Optional[str] = None
    interpretation: Optional[Dict[str, Any]] = None
    accounting: Optional[Dict[str, Any]] = None
    # Phase 7E persistence outcome — explicit, never silently discarded:
    persisted: bool = False
    persistence_error: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# Developer API (Phase 13 — versioned hosted boundary over the public
# developer interface; transport only, no status authority)
# ---------------------------------------------------------------------------


class DeveloperProcessResponse(BaseModel):
    """
    Stable versioned projection of a public-interface result.

    The state taxonomy is the Kernel's own, carried verbatim:
        VERIFIED, REVIEW_REQUIRED, BLOCKED, VALIDATION_FAILED,
        GROUNDING_FAILED, FORBIDDEN_OUTPUT, MODEL_UNAVAILABLE,
        UNSUPPORTED_TRANSACTION

    The HTTP layer can never create or upgrade states — the ``status``
    field is a verbatim copy of the Kernel's terminal state.

    Phase 5A adds the six-state PUBLIC API status alongside it (additive,
    back-compatible): ``api_status`` is the deterministic transport-layer
    mapping of the engine state onto the closed six-state contract
    (PROCESSING / VERIFIED / REVIEW_REQUIRED / UNSUPPORTED /
    INVALID_INPUT / FAILED). The engine ``status`` remains authoritative;
    ``api_status`` is derived from it by ``api.status`` and adds no
    financial semantics.
    """

    api_version: str = "v1"
    request_id: Optional[str] = None
    status: str
    status_label: str = ""
    success: bool = False
    next_action: Optional[str] = None
    issues: List[str] = Field(default_factory=list)
    grounding_issues: List[str] = Field(default_factory=list)
    rule_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    interpretation: Optional[Dict[str, Any]] = None
    accounting: Optional[Dict[str, Any]] = None
    # --- Phase 5A: six-state public API status (mapping only) ---
    api_status: str = ""
    api_status_label: str = ""
    retryable: bool = False
    reason_code: Optional[str] = None
    # Verbatim engine terminal state, carried beside the public mapping
    # so relabeling never loses information (None for pure transport
    # errors, which never reached the engine).
    engine_status: Optional[str] = None


class DeveloperDocumentProcessResponse(BaseModel):
    """Versioned response for the document submission path.

    Transport + provenance only. ``status`` is the Kernel's own terminal
    state carried verbatim — the document layer can never create, upgrade
    or soften it, and OCR output can never reach VERIFIED.

    ``document`` exposes the evidence a developer needs to audit a result:
    page count, per-page extraction status, which pages still need OCR, the
    OCR engine used, and citable evidence (page / bbox / text /
    confidence) for the supported semantic fields.
    """

    api_version: str = "v1"
    request_id: Optional[str] = None
    status: str
    status_label: str = ""
    success: bool = False
    next_action: Optional[str] = None
    issues: List[str] = Field(default_factory=list)
    grounding_issues: List[str] = Field(default_factory=list)
    rule_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    interpretation: Optional[Dict[str, Any]] = None
    accounting: Optional[Dict[str, Any]] = None
    # --- Phase 5A: six-state public API status (mapping only) ---
    api_status: str = ""
    api_status_label: str = ""
    retryable: bool = False
    reason_code: Optional[str] = None
    # Verbatim engine terminal state, carried beside the public mapping
    # so relabeling never loses information (None for pure transport
    # errors, which never reached the engine).
    engine_status: Optional[str] = None

    # --- document provenance (never a financial claim) ---
    document: Dict[str, Any] = Field(default_factory=dict)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    timings_ms: Dict[str, Any] = Field(default_factory=dict)
    notes: List[str] = Field(default_factory=list)


class DeveloperCapabilityEntry(BaseModel):
    """One capability record, EXACTLY as the registry serializes it.

    Phase 5B: this is a verbatim projection of
    ``backend.maths.capability_registry.Capability.to_metadata()`` —
    the registry's own field names, types, and semantics. The API layer
    renames nothing, reinterprets nothing, and invents nothing: a field
    the registry leaves empty stays empty here.
    """

    capability_id: str
    authority: str
    canonical_name: str
    supported_status: str
    description: str = ""
    required_inputs: List[str] = Field(default_factory=list)
    deterministic_op: str = ""
    implementation_ref: str = ""
    test_ref: str = ""
    source_ref: str = ""
    jurisdiction: str = "general"
    framework: str = ""
    version: str = "1.0"
    limitations: List[str] = Field(default_factory=list)


class DeveloperCapabilitiesResponse(BaseModel):
    """Phase 5B capability discovery — a READ-ONLY adapter over the live
    capability registry (``backend/maths/capability_registry.py``).

    The registry is the single source of truth: this response is derived
    from it at request time (deterministic, ``capability_id``-ordered).
    The API layer maintains no capability list of its own and never
    collapses, renames, or upgrades registry statuses — UNSUPPORTED and
    PLANNED are legitimate capability metadata, not API failures.

    Discovery is a read operation: no model inference, no grounding, no
    authority execution. ``api_status`` is the six-state public transport
    status of THIS read (VERIFIED = the deterministic registry read
    succeeded); ``engine_status`` stays None because no engine ran.
    """

    api_version: str = "v1"
    request_id: Optional[str] = None
    # --- Phase 5A public status trio (transport read outcome only) ---
    api_status: str = ""
    api_status_label: str = ""
    retryable: bool = False
    # No engine participates in discovery — always None here.
    engine_status: Optional[str] = None
    # Derived live from registry.summary(): authority x status counts.
    registry_summary: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    count: int = 0
    capabilities: List[DeveloperCapabilityEntry] = Field(default_factory=list)


class DeveloperHealthResponse(BaseModel):
    """Liveness — the API process is alive. Touches nothing."""

    status: str
    service: str
    api_version: str


class DeveloperReadyResponse(BaseModel):
    """Readiness — dependencies available enough to process a request."""

    status: str
    api_version: str
    provider: Dict[str, Any]
    rule_pack: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
