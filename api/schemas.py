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
