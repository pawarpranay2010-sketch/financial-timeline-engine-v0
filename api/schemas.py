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
