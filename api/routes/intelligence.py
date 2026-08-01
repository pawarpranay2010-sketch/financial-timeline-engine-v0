"""Stage 2 — Agentic RAG analysis endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

import api.services as svc
from api.schemas import AnalyzeRequest, AnalysisResponse

router = APIRouter(tags=["intelligence"])


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
        raise HTTPException(
            status_code=500,
            detail=f"Analysis failed: {type(exc).__name__}: {str(exc)[:300]}",
        )
    return AnalysisResponse(**result)


@router.post("/db/init")
def init_db() -> dict:
    """Explicitly create/verify the database schema (on-demand, never at startup)."""
    return svc.initialize_database_schema()
