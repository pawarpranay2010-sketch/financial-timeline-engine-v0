"""Stage 2 — service layer bridging to the Phase 6 intelligence pipeline.

ALL Phase 6 imports are lazy (inside functions) so that FastAPI can bind
port 5000 quickly without waiting on database connections, provider
initialization, or Redis. No heavy module is imported at module scope.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

APP_STARTED_AT = time.monotonic()

# Names we surface in health/status responses — never values.
FINANCIAL_ENV_VARS = {
    "fmp": "FMP_API_KEY",
    "finnhub": "FINNHUB_API_KEY",
    "alpha_vantage": "ALPHA_VANTAGE_API_KEY",
    "polygon": "POLYGON_API_KEY",
}

AI_ENV_VARS = {
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "rapidapi": "RAPIDAPI_KEY",
    "sambanova": "SAMBANOVA_API_KEY",
    "github": "GITHUB_TOKEN",
    "cerebras": "CEREBRAS_API_KEY",
    "cohere": "COHERE_API_KEY",
}


def uptime_seconds() -> float:
    return round(time.monotonic() - APP_STARTED_AT, 2)


def _key_status_map(env_map: Dict[str, str]) -> Dict[str, Any]:
    """Report which keys are configured (names only — never values)."""
    out: Dict[str, Any] = {}
    for name, var in env_map.items():
        out[name] = {"key_configured": bool(os.getenv(var, "")), "env_var": var}
    return out


def database_status() -> Dict[str, Any]:
    """
    Lightweight, non-blocking database connectivity check.

    Does a single `SELECT 1` through the existing engine with a short
    timeout. Never raises — failures are reported in the payload so the
    API stays up even if Postgres is temporarily unreachable.
    """
    try:
        import sqlalchemy as sa
        from sqlalchemy import text

        # Import lazily: backend.database.db raises at import time when
        # DATABASE_URL is missing, so we guard that here.
        try:
            from backend.database.db import engine
        except Exception as exc:  # e.g. missing DATABASE_URL
            return {
                "configured": False,
                "reachable": False,
                "error": f"Database layer unavailable: {type(exc).__name__}",
            }

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"configured": True, "reachable": True, "error": None}
    except Exception as exc:
        return {
            "configured": True,
            "reachable": False,
            "error": f"{type(exc).__name__}: {str(exc)[:200]}",
        }


def redis_status() -> Dict[str, Any]:
    """Report whether Redis is configured (never connects at startup)."""
    url = os.getenv("REDIS_URL", "")
    return {
        "configured": bool(url),
        "enabled": bool(url),
        "note": "Redis is optional; the app degrades to PostgreSQL + provider cache when absent.",
    }


def provider_key_status() -> Dict[str, Any]:
    """Masked provider key presence for the frontend status panel."""
    return {
        "financial": _key_status_map(FINANCIAL_ENV_VARS),
        "ai": _key_status_map(AI_ENV_VARS),
    }


# ---------------------------------------------------------------------------
# Market data (DataAgent → ProviderOrchestrator → PostgreSQL/Redis cache)
# ---------------------------------------------------------------------------

def fetch_market_snapshot(ticker: str) -> Dict[str, Any]:
    """
    Fetch a full company intelligence snapshot via DataAgent (lazy import).

    DataAgent uses the existing ProviderOrchestrator failover chain:
    PostgreSQL cache → Redis cache → live providers — all existing Phase 6
    behavior, invoked only when a request arrives.
    """
    ticker = ticker.strip().upper()
    start = time.monotonic()
    try:
        from backend.intelligence.data_agent import DataAgent

        agent = DataAgent(ticker)
        result = agent.fetch_all()
        latency_ms = round((time.monotonic() - start) * 1000)
        ok = result.get("company_profile", {}).get("success", False) or bool(
            result.get("market_price", {}).get("data")
        )
        return {
            "ticker": ticker,
            "success": ok,
            "data": result,
            "latency_ms": latency_ms,
            "error": None,
        }
    except Exception as exc:
        latency_ms = round((time.monotonic() - start) * 1000)
        return {
            "ticker": ticker,
            "success": False,
            "data": {},
            "latency_ms": latency_ms,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }


# ---------------------------------------------------------------------------
# Agentic RAG analysis
# ---------------------------------------------------------------------------

def run_analysis(ticker: str, goal: str, max_iterations: int = 3) -> Dict[str, Any]:
    """
    Run the Agentic RAG pipeline (frozen Phase 6 component) for a goal.

    The orchestrator reads evidence from PostgreSQL (RetrievalAgent) and
    live providers (DataAgent), validates it through the SourceResolver /
    CurrencyValidator / ExtractionAuditor gates, and returns a canonical
    evidence set. Imported lazily so startup never blocks on it.
    """
    ticker = ticker.strip().upper()
    from backend.intelligence.agentic_rag_orchestrator import AgenticRAGOrchestrator

    orchestrator = AgenticRAGOrchestrator(
        ticker=ticker,
        max_iterations=max_iterations,
    )
    canonical = orchestrator.execute(goal)
    payload = canonical.to_dict()
    return {
        "ticker": ticker,
        "goal": goal,
        "terminal_state": payload.get("terminal_state", ""),
        "terminal_reason": payload.get("terminal_reason"),
        "iterations_used": payload.get("iterations_used", 0),
        "evidence_count": payload.get("evidence_count", 0),
        "resolved_count": payload.get("resolved_count", 0),
        "resolved_facts": payload.get("resolved_facts", []),
        "summary_text": canonical.get_summary_text(),
    }


# ---------------------------------------------------------------------------
# Database initialization (explicitly triggered, never at startup)
# ---------------------------------------------------------------------------

def initialize_database_schema() -> Dict[str, Any]:
    """Create all SQLAlchemy tables. Called on-demand via POST /api/v1/db/init."""
    try:
        from backend.database.init_db import initialize_database

        initialize_database()
        return {"success": True, "error": None}
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
