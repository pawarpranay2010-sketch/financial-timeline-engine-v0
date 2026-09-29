"""Stage 2 — Agentic RAG analysis endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

import api.services as svc
from api.routes._admission import require_api_v1_credential
from api.schemas import AnalyzeRequest, AnalysisResponse

# Security hardening (audit H-01, 2026-09-29): both routes below are
# state-changing or provider-backed and must not be anonymous.
router = APIRouter(
    tags=["intelligence"],
    dependencies=[Depends(require_api_v1_credential)],
)


@router.post("/intelligence/analyze", response_model=AnalysisResponse)
def analyze(req: AnalyzeRequest) -> AnalysisResponse:
    """
    Run the Agentic RAG pipeline for a company goal.

    Executes the frozen Phase 6 AgenticRAGOrchestrator: requirements
    generation → retrieval loop → source resolution → currency
    validation → extraction audit → canonical evidence set.
    """
    try:
        result = svc.run_analysis(
            ticker=req.ticker,
            goal=req.goal,
            max_iterations=req.max_iterations,
        )
    except Exception as exc:
        # SECURITY (audit M-04, 2026-09-29): this route is reachable by any
        # caller holding a valid API key, and the previous handler returned
        # f"...{type(exc).__name__}: {str(exc)[:300]}" to the client. A
        # single controlled exception leaked a database DSN, SQL, a
        # filesystem path, a model prompt and an API key to the caller.
        # The detail now goes to the server log; the client gets a stable
        # generic message. The HTTP status is unchanged.
        import logging

        logging.getLogger("platrixa.api").exception(
            "analysis failed for %s: %s", type(exc).__name__, exc
        )
        raise HTTPException(status_code=500, detail="Analysis failed")
    return AnalysisResponse(**result)


@router.post("/db/init")
def init_db() -> dict:
    """Explicitly create/verify the database schema (on-demand, never at startup)."""
    return svc.initialize_database_schema()
